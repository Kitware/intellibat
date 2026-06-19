#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "hardware/adc.h"
#include "hardware/dma.h"
#include "hardware/gpio.h"
#include "hardware/irq.h"
#include "hardware/uart.h"
#include "pico/error.h"
#include "pico/stdio.h"
#include "pico/stdio/driver.h"
#include "pico/stdio_usb.h"
#include "pico/stdlib.h"

#ifndef DEFAULT_SAMPLE_RATE_HZ
#define DEFAULT_SAMPLE_RATE_HZ 384000
#endif

#ifndef ADC_GPIO
#define ADC_GPIO 26
#endif

#ifndef ADC_CHANNEL
#define ADC_CHANNEL 0
#endif

#ifndef DEBUG_UART
#define DEBUG_UART uart0
#endif

#ifndef DEBUG_UART_TX_PIN
#define DEBUG_UART_TX_PIN 0
#endif

#ifndef DEBUG_UART_RX_PIN
#define DEBUG_UART_RX_PIN 1
#endif

#ifndef DEBUG_UART_BAUD
#define DEBUG_UART_BAUD 115200
#endif

#define CMD_BUFFER_LEN 32u
#define MIN_SAMPLE_RATE_HZ 1000u
#define MAX_SAMPLE_RATE_HZ 384000u
#define ADC_CLOCK_HZ 48000000.0f

#define FRAME_MAGIC_0 'I'
#define FRAME_MAGIC_1 'B'
#define FRAME_MAGIC_2 'A'
#define FRAME_MAGIC_3 'T'
#define FRAME_HEADER_LEN 16u
#define FRAME_SAMPLE_COUNT 1024u
#define FRAME_PAYLOAD_LEN (FRAME_SAMPLE_COUNT * sizeof(int16_t))
#define FRAME_TX_BUFFER_LEN (FRAME_HEADER_LEN + FRAME_PAYLOAD_LEN)
#define CAPTURE_BLOCK_COUNT 64u
#define DMA_DISCARD_BLOCK UINT32_MAX

enum
{
  BLOCK_FREE = 0,
  BLOCK_FILLING = 1,
  BLOCK_READY = 2,
  BLOCK_SENDING = 3,
};

static uint16_t capture_blocks[CAPTURE_BLOCK_COUNT][FRAME_SAMPLE_COUNT];
static uint16_t discard_block[FRAME_SAMPLE_COUNT];

static volatile uint8_t block_state[CAPTURE_BLOCK_COUNT];
static volatile uint32_t block_sequence[CAPTURE_BLOCK_COUNT];
static volatile uint32_t active_dma_block = DMA_DISCARD_BLOCK;
static volatile uint32_t next_capture_sequence = 0;
static volatile uint32_t dropped_samples = 0;
static volatile bool streaming_enabled = false;

static uint32_t frame_sequence = 0;
static uint32_t sample_rate_hz = DEFAULT_SAMPLE_RATE_HZ;
static int32_t dc_estimate_q8 = 2048 * 256;
static int dma_channel = -1;
static uint8_t frame_tx_buffer[FRAME_TX_BUFFER_LEN];

static void debug_puts(const char* message)
{
  uart_puts(DEBUG_UART, message);
}

static void debug_printf(const char* fmt, uint32_t value)
{
  char buf[80];
  snprintf(buf, sizeof(buf), fmt, value);
  debug_puts(buf);
}

static void store_u16_le(uint8_t* buffer, uint32_t offset, uint16_t value)
{
  buffer[offset] = value & 0xffu;
  buffer[offset + 1u] = (value >> 8) & 0xffu;
}

static void store_u32_le(uint8_t* buffer, uint32_t offset, uint32_t value)
{
  buffer[offset] = value & 0xffu;
  buffer[offset + 1u] = (value >> 8) & 0xffu;
  buffer[offset + 2u] = (value >> 16) & 0xffu;
  buffer[offset + 3u] = (value >> 24) & 0xffu;
}

static int16_t convert_adc_sample(uint16_t raw)
{
  int32_t raw_q8 = (int32_t)(raw & 0x0fffu) * 256;
  dc_estimate_q8 += (raw_q8 - dc_estimate_q8) >> 10;

  int32_t centered = (raw_q8 - dc_estimate_q8) >> 4;
  if (centered > INT16_MAX) {
    centered = INT16_MAX;
  } else if (centered < INT16_MIN) {
    centered = INT16_MIN;
  }

  return (int16_t)centered;
}

static void clear_capture_blocks(void)
{
  for (uint32_t i = 0; i < CAPTURE_BLOCK_COUNT; i++) {
    block_state[i] = BLOCK_FREE;
    block_sequence[i] = 0;
  }
  active_dma_block = DMA_DISCARD_BLOCK;
  next_capture_sequence = 0;
  frame_sequence = 0;
}

static int find_free_block(void)
{
  for (uint32_t i = 0; i < CAPTURE_BLOCK_COUNT; i++) {
    if (block_state[i] == BLOCK_FREE) {
      return (int)i;
    }
  }
  return -1;
}

static int find_next_ready_block(void)
{
  int best_block = -1;
  uint32_t best_sequence = UINT32_MAX;

  for (uint32_t i = 0; i < CAPTURE_BLOCK_COUNT; i++) {
    if (block_state[i] == BLOCK_READY && block_sequence[i] < best_sequence) {
      best_sequence = block_sequence[i];
      best_block = (int)i;
    }
  }

  return best_block;
}

static void start_dma_capture_block(void)
{
  int next_block = find_free_block();
  uint16_t* write_addr = discard_block;

  if (next_block >= 0) {
    active_dma_block = (uint32_t)next_block;
    block_state[next_block] = BLOCK_FILLING;
    write_addr = capture_blocks[next_block];
  } else {
    active_dma_block = DMA_DISCARD_BLOCK;
  }

  dma_channel_set_write_addr(dma_channel, write_addr, false);
  dma_channel_set_trans_count(dma_channel, FRAME_SAMPLE_COUNT, true);
}

static void dma_irq_handler(void)
{
  dma_hw->ints0 = 1u << dma_channel;

  if (active_dma_block == DMA_DISCARD_BLOCK) {
    dropped_samples += FRAME_SAMPLE_COUNT;
    next_capture_sequence++;
  } else {
    uint32_t finished_block = active_dma_block;
    block_sequence[finished_block] = next_capture_sequence++;
    block_state[finished_block] = BLOCK_READY;
  }

  if (streaming_enabled) {
    start_dma_capture_block();
  }
}

static void stop_adc_capture(void)
{
  streaming_enabled = false;
  adc_run(false);
  if (dma_channel >= 0) {
    dma_channel_abort(dma_channel);
    dma_hw->ints0 = 1u << dma_channel;
  }
  adc_fifo_drain();
  clear_capture_blocks();
}

static void start_adc_capture(void)
{
  stop_adc_capture();
  dc_estimate_q8 = 2048 * 256;
  dropped_samples = 0;
  streaming_enabled = true;
  adc_fifo_drain();
  start_dma_capture_block();
  adc_run(true);
}

static void restart_adc_sampler(uint32_t new_sample_rate_hz)
{
  bool was_streaming = streaming_enabled;

  if (new_sample_rate_hz < MIN_SAMPLE_RATE_HZ) {
    new_sample_rate_hz = MIN_SAMPLE_RATE_HZ;
  } else if (new_sample_rate_hz > MAX_SAMPLE_RATE_HZ) {
    new_sample_rate_hz = MAX_SAMPLE_RATE_HZ;
  }

  if (was_streaming) {
    stop_adc_capture();
  }

  sample_rate_hz = new_sample_rate_hz;
  // Pico SDK adc_set_clkdiv uses sample period cycles: period = 1 + clkdiv.
  float clkdiv = (ADC_CLOCK_HZ / (float)sample_rate_hz) - 1.0f;
  if (clkdiv < 0.0f) {
    clkdiv = 0.0f;
  }
  adc_set_clkdiv(clkdiv);

  if (was_streaming) {
    start_adc_capture();
  }

  debug_printf("sample_rate_hz=%u\r\n", sample_rate_hz);
}

static void handle_command(char* cmd)
{
  while (isspace((unsigned char)*cmd)) {
    cmd++;
  }

  for (char* p = cmd; *p; p++) {
    *p = (char)toupper((unsigned char)*p);
  }

  if (strcmp(cmd, "START") == 0) {
    start_adc_capture();
    debug_puts("streaming=1\r\n");
    return;
  }

  if (strcmp(cmd, "STOP") == 0) {
    streaming_enabled = false;
    stop_adc_capture();
    debug_puts("streaming=0\r\n");
    return;
  }

  if (strncmp(cmd, "SET_SR:", 7) == 0) {
    uint32_t new_rate = 0;
    for (const char* p = cmd + 7; *p; p++) {
      if (!isdigit((unsigned char)*p)) {
        debug_puts("invalid_sample_rate\r\n");
        return;
      }
      new_rate = (new_rate * 10u) + (uint32_t)(*p - '0');
    }
    restart_adc_sampler(new_rate);
    return;
  }

  if (strcmp(cmd, "STATUS") == 0) {
    debug_printf("streaming=%u\r\n", streaming_enabled ? 1u : 0u);
    debug_printf("sample_rate_hz=%u\r\n", sample_rate_hz);
    debug_printf("dropped_samples=%u\r\n", dropped_samples);
    return;
  }

  debug_puts("unknown_command\r\n");
}

static void poll_usb_commands(void)
{
  static char cmd_buffer[CMD_BUFFER_LEN];
  static size_t cmd_len = 0;

  while (true) {
    int ch = getchar_timeout_us(0);
    if (ch == PICO_ERROR_TIMEOUT) {
      break;
    }

    if (ch == '\r' || ch == '\n') {
      if (cmd_len > 0) {
        cmd_buffer[cmd_len] = '\0';
        handle_command(cmd_buffer);
        cmd_len = 0;
      }
      continue;
    }

    if (cmd_len < CMD_BUFFER_LEN - 1u) {
      cmd_buffer[cmd_len++] = (char)ch;
    } else {
      cmd_len = 0;
      debug_puts("command_too_long\r\n");
    }
  }
}

static void build_frame_header(uint8_t* buffer, uint16_t sample_count)
{
  buffer[0] = FRAME_MAGIC_0;
  buffer[1] = FRAME_MAGIC_1;
  buffer[2] = FRAME_MAGIC_2;
  buffer[3] = FRAME_MAGIC_3;
  store_u32_le(buffer, 4, frame_sequence);
  store_u16_le(buffer, 8, sample_count);
  store_u16_le(buffer, 10, 0);
  store_u32_le(buffer, 12, dropped_samples);
}

static void build_frame_samples(uint8_t* buffer,
                                uint16_t* samples,
                                uint16_t sample_count)
{
  for (uint16_t i = 0; i < sample_count; i++) {
    int16_t sample = convert_adc_sample(samples[i]);
    store_u16_le(buffer,
                 FRAME_HEADER_LEN + ((uint32_t)i * sizeof(int16_t)),
                 (uint16_t)sample);
  }
}

static void write_usb_bytes(const uint8_t* buffer, uint32_t length)
{
  stdio_usb.out_chars((const char*)buffer, (int)length);
}

static void clear_ready_blocks(void)
{
  for (uint32_t i = 0; i < CAPTURE_BLOCK_COUNT; i++) {
    if (block_state[i] == BLOCK_READY) {
      block_state[i] = BLOCK_FREE;
    }
  }
}

static void drain_frames_to_usb(void)
{
  if (!stdio_usb_connected()) {
    clear_ready_blocks();
    return;
  }

  while (true) {
    int block = find_next_ready_block();
    if (block < 0) {
      break;
    }

    block_state[block] = BLOCK_SENDING;
    build_frame_header(frame_tx_buffer, FRAME_SAMPLE_COUNT);
    build_frame_samples(
      frame_tx_buffer, capture_blocks[block], FRAME_SAMPLE_COUNT);
    write_usb_bytes(frame_tx_buffer, FRAME_TX_BUFFER_LEN);
    frame_sequence++;
    block_state[block] = BLOCK_FREE;
  }
}

static void init_debug_uart(void)
{
  uart_init(DEBUG_UART, DEBUG_UART_BAUD);
  gpio_set_function(DEBUG_UART_TX_PIN, GPIO_FUNC_UART);
  gpio_set_function(DEBUG_UART_RX_PIN, GPIO_FUNC_UART);
}

static void init_adc(void)
{
  adc_init();
  adc_gpio_init(ADC_GPIO);
  adc_select_input(ADC_CHANNEL);
  adc_fifo_setup(true, true, 1, false, false);
}

static void init_dma(void)
{
  dma_channel = dma_claim_unused_channel(true);
  dma_channel_config config = dma_channel_get_default_config(dma_channel);
  channel_config_set_transfer_data_size(&config, DMA_SIZE_16);
  channel_config_set_read_increment(&config, false);
  channel_config_set_write_increment(&config, true);
  channel_config_set_dreq(&config, DREQ_ADC);

  dma_channel_configure(dma_channel,
                        &config,
                        discard_block,
                        &adc_hw->fifo,
                        FRAME_SAMPLE_COUNT,
                        false);

  dma_channel_set_irq0_enabled(dma_channel, true);
  irq_set_exclusive_handler(DMA_IRQ_0, dma_irq_handler);
  irq_set_enabled(DMA_IRQ_0, true);
}

int main(void)
{
  init_debug_uart();
  stdio_usb_init();
  stdio_set_translate_crlf(&stdio_usb, false);
  init_adc();
  init_dma();

  debug_puts("intellibat boot\r\n");
  debug_printf("adc_gpio=%u\r\n", ADC_GPIO);
  restart_adc_sampler(sample_rate_hz);

  while (true) {
    poll_usb_commands();
    drain_frames_to_usb();
    tight_loop_contents();
  }
}
