cmake_minimum_required(VERSION 3.12)

# Pull in SDK (must be before project)
include(pico_sdk_import.cmake)
include(pico_extras_import_optional.cmake)

project(adc C CXX ASM)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

if(PICO_SDK_VERSION_STRING VERSION_LESS "2.2.0")
  message(
    FATAL_ERROR
      "Raspberry Pi Pico SDK version 2.2.0 (or later) required. Your version is ${PICO_SDK_VERSION_STRING}"
  )
endif()

set(PICO_EXAMPLES_PATH ${PROJECT_SOURCE_DIR})

# If you want debug output from USB (pass -DPICO_STDIO_USB=1) this ensures you
# don't lose any debug output while USB is set up
if(NOT DEFINED PICO_STDIO_USB_CONNECT_WAIT_TIMEOUT_MS)
  set(PICO_STDIO_USB_CONNECT_WAIT_TIMEOUT_MS 3000)
endif()

# Allow using the example keys, as this is the examples repository
if(NOT DEFINED PICO_ALLOW_EXAMPLE_KEYS)
  set(PICO_ALLOW_EXAMPLE_KEYS 1)
endif()

# Initialize the SDK
pico_sdk_init()

include(example_auto_set_url.cmake)

add_compile_options(
  -Wall -Wno-format # int != int32_t as far as the compiler is concerned because
                    # gcc has int32_t as long int
  -Wno-unused-function # we have some for the docs that aren't called
)

if(CMAKE_C_COMPILER_ID STREQUAL "GNU")
  add_compile_options(-Wno-maybe-uninitialized)
endif()

add_executable(adc adc.c)

target_compile_definitions(
  adc
  PRIVATE DEFAULT_SAMPLE_RATE_HZ=384000
          ADC_GPIO=26
          ADC_CHANNEL=0
          DEBUG_UART=uart0
          DEBUG_UART_TX_PIN=0
          DEBUG_UART_RX_PIN=1
          DEBUG_UART_BAUD=115200)

# pull in common dependencies and adc hardware support
target_link_libraries(adc PRIVATE pico_stdlib hardware_adc hardware_dma)

# create map/bin/hex file etc.
pico_enable_stdio_usb(adc 1)
pico_enable_stdio_uart(adc 1)

pico_add_extra_outputs(adc)

# add url via pico_set_program_url
example_auto_set_url(adc)
