'use strict';

const $ = (id) => document.getElementById(id);
const number = (value) => value == null ? 'Unavailable' : Number(value).toLocaleString();
const bytes = (value) => {
    if (value == null || !Number.isFinite(Number(value))) return 'Unavailable';
    const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    let size = Number(value), unit = 0;
    while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit++; }
    return `${size.toFixed(unit ? 1 : 0)} ${units[unit]}`;
};
const duration = (value) => {
    if (value == null) return 'Unavailable';
    const seconds = Math.max(0, Math.round(value));
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
    return `${Math.floor(seconds / 86400)}d ${Math.floor(seconds % 86400 / 3600)}h`;
};
const dateTime = (value, timezone) => value == null ? 'Unavailable' : new Date(value * 1000).toLocaleString([], {timeZone: timezone, timeZoneName: 'short'});
const measured = (value, unit, digits = 1) => value == null ? 'Unavailable' : `${Number(value).toFixed(digits)} ${unit}`;
const enabled = (value) => value == null ? 'Unavailable' : value ? 'Enabled' : 'Disabled';
function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
}
function empty(target, message) { target.replaceChildren(element('p', message, 'empty')); }
function details(rows) {
    const list = element('dl', null, 'detail-list');
    for (const [label, value] of rows) list.append(element('dt', label), element('dd', value == null || value === '' ? 'Unavailable' : value));
    return list;
}
function metric(label, value, note) {
    const card = element('div', null, 'metric');
    card.append(element('div', label, 'metric-label'), element('div', value, 'metric-value'), element('div', note, 'metric-note'));
    return card;
}
function notice(id, message) { $(id).textContent = message || ''; $(id).hidden = !message; }
async function api(path, body) {
    const response = await fetch(path, body === undefined ? {cache: 'no-store'} : {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    let result;
    try { result = await response.json(); } catch { throw new Error(`Device returned HTTP ${response.status}. Please retry.`); }
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : `Request failed (HTTP ${response.status}). Check your inputs.`);
    return result;
}
function refreshed(timestamp, timezone) { $('updated-at').textContent = `Updated ${dateTime(timestamp, timezone)}`; }
function action(id, callback, errorId = 'page-error') {
    $(id).addEventListener('click', async () => {
        const button = $(id); button.disabled = true; notice(errorId, '');
        try { await callback(); } catch (error) { notice(errorId, error.message); }
        finally { button.disabled = false; if (document.body.dataset.page === 'data') copyControls(); }
    });
}

function renderStatus(data) {
    const runtime = data.runtime;
    const recorder = data.services.find((service) => service.name === 'intellibat.service');
    const unhealthy = !runtime || runtime.stale || runtime.audio_stalled;
    const activity = !runtime ? 'Unavailable' : runtime.stale ? 'Stale heartbeat' : runtime.audio_stalled ? 'Audio stalled' : runtime.recording ? 'Recording' : runtime.streaming ? 'Listening' : 'Scheduled idle';
    const battery = data.power_supplies.find((supply) => supply.type === 'Battery' && supply.present !== 0);
    $('status-summary').replaceChildren(
        metric('RECORDER', activity, recorder?.available ? `Service: ${recorder.active} / ${recorder.substate}` : 'Service status unavailable'),
        metric('CPU UTILIZATION', data.cpu_percent == null ? '—' : `${data.cpu_percent}%`, `${number(data.cpu_count)} cores · ${measured(data.cpu_mhz, 'MHz', 0)}`),
        metric('DEVICE UPTIME', duration(data.uptime_seconds), data.hostname),
        metric('BATTERY', battery?.capacity == null ? '—' : `${battery.capacity}%`, battery ? `${battery.status || 'Status unavailable'} · ${measured(battery.voltage_now, 'V', 2)}` : 'No battery telemetry reported'),
    );
    $('heartbeat-state').textContent = runtime ? `Heartbeat ${duration(runtime.age_seconds)} ago` : 'Heartbeat unavailable';
    $('heartbeat-state').className = `badge${unhealthy ? ' warn' : ''}`;
    const runtimeNode = $('runtime-status'); runtimeNode.replaceChildren();
    if (runtime) {
        const grid = element('div', null, 'runtime-grid');
        grid.append(details([
            ['Recorder state', activity], ['Current file', runtime.current_recording || 'No active recording'],
            ['Last audio received', dateTime(runtime.last_audio_at)], ['Last completed recording', dateTime(runtime.last_recording_at)],
            ['Recordings this session', number(runtime.recordings_completed)], ['Serial sample rate', measured(runtime.sample_rate, 'Hz', 0)],
            ['Recording schedule', `${runtime.schedule_start || '—'} → ${runtime.schedule_end || '—'}`],
            ['Schedule mode', runtime.schedule_mode], ['Triggered recording', enabled(runtime.triggered_recording)],
        ]), details([
            ['Spectrogram queue', number(runtime.spectrogram_queue)], ['ML queue', number(runtime.classifier_queue)],
            ['Buffered audio', bytes(runtime.buffered_bytes)], ['Dropped samples (Pico)', number(runtime.dropped_samples)],
            ['LED', enabled(runtime.led_enabled)], ['Machine learning', enabled(runtime.machine_learning_enabled)],
            ['Configuration applied', dateTime(runtime.config_loaded_at)], ['Last ML result', dateTime(runtime.last_classification_at)],
            ['Last spectrogram', dateTime(runtime.last_spectrogram_at)], ['Startup recovery', runtime.recovery_state],
            ['Recovered jobs', runtime.recovered_jobs ? `${number(runtime.recovered_jobs.spectrogram)} WAVs · ${number(runtime.recovered_jobs.classifier)} images` : 'Unavailable'],
            ['Worker threads', Object.entries(runtime.threads || {}).map(([name, alive]) => `${name}: ${alive ? 'running' : 'stopped'}`).join(' · ')],
        ]));
        runtimeNode.append(grid);
        const workers = element('div', null, 'runtime-grid worker-details');
        for (const [name, worker] of Object.entries(runtime.processing_workers || {})) {
            const card = element('div');
            const state = runtime.threads?.[name] === false ? 'stopped' : worker.state;
            card.append(element('h3', `${name === 'spectrogram' ? 'Spectrogram' : 'ML classifier'} · ${state}`));
            card.append(details([
                ['Current file', worker.current_file || 'Waiting for work'],
                ['Completed this session', number(worker.completed)], ['Already complete', number(worker.skipped)],
                ['Failed attempts / retries', `${number(worker.failures)} / ${number(worker.retries)}`],
                ['Last completion', dateTime(worker.last_completed_at)],
            ]));
            if (state === 'paused') card.append(element('p', 'Queued images will be processed when machine learning is enabled in Settings.', 'notice'));
            if (worker.last_error) card.append(element('p', `Last processing error (${dateTime(worker.last_error_at)}): ${worker.last_error}`, 'notice error'));
            workers.append(card);
        }
        runtimeNode.append(workers);
        if (runtime.stale) runtimeNode.prepend(element('p', 'The recorder heartbeat is stale. Values below are the last reported values.', 'notice error'));
        if (runtime.audio_stalled) runtimeNode.prepend(element('p', 'The device is expected to be streaming, but no audio has arrived in the last 10 seconds.', 'notice error'));
        if (runtime.last_error) runtimeNode.append(element('p', runtime.last_error, 'notice error'));
        if (runtime.uart_message) runtimeNode.append(element('p', `Last UART message: ${runtime.uart_message}`, 'filename'));
    } else empty(runtimeNode, data.runtime_error || 'Recorder heartbeat unavailable. Start or update the recording service.');
    const table = element('table'), head = element('thead'), header = element('tr');
    for (const label of ['Service', 'State', 'Detail / result', 'PID', 'Restarts', 'Memory', 'Active since']) header.append(element('th', label));
    head.append(header); table.append(head);
    const body = element('tbody');
    for (const service of data.services) {
        const row = element('tr');
        for (const value of [service.name, service.available ? service.active : 'Unavailable', service.available ? `${service.substate} / ${service.result}` : 'systemd is not reporting', service.pid || '—', service.restarts || '—', service.memory_bytes && service.memory_bytes !== '[not set]' && Number(service.memory_bytes) < 2 ** 63 ? bytes(service.memory_bytes) : '—', service.since || '—']) row.append(element('td', value));
        body.append(row);
    }
    table.append(body); $('service-status').replaceChildren(table);
    $('hardware-status').replaceChildren(details([
        ['Model', data.model], ['Hostname', data.hostname], ['Architecture', data.architecture],
        ['Kernel', data.kernel], ['Operating system', data.os], ['CPU cores', number(data.cpu_count)],
        ['CPU frequency', measured(data.cpu_mhz, 'MHz', 0)],
        ['Load average (1 / 5 / 15 min)', data.load_average.map((value) => value.toFixed(2)).join(' / ')],
        ...data.serial_devices.map((device) => [device.path, device.present ? 'Present' : 'Not connected']),
    ]));
    const memory = data.memory;
    $('memory-status').replaceChildren(details([
        ['RAM used / total', `${bytes(memory.used)} / ${bytes(memory.total)}`], ['RAM available', bytes(memory.available)],
        ['Swap free / total', `${bytes(memory.swap_free)} / ${bytes(memory.swap_total)}`],
        ...(data.temperatures.length ? data.temperatures.map((sensor) => [sensor.name, measured(sensor.celsius, '°C')]) : [['Temperature', 'No sensor reported']]),
        ...(data.fans.length ? data.fans.map((fan) => [`${fan.name} fan`, measured(fan.rpm, 'RPM', 0)]) : [['Fan speed', 'No sensor reported']]),
        ['Current power / thermal flags', data.throttling.available ? data.throttling.current.join(', ') || 'None' : 'Unavailable'],
        ['Flags since boot', data.throttling.available ? data.throttling.since_boot.join(', ') || 'None' : 'Unavailable'],
    ]));
    const power = $('power-status'); power.replaceChildren();
    if (!battery) power.append(element('p', 'No battery level is reported by Linux. A battery may be connected without a supported telemetry driver.', 'notice'));
    const supplyFields = [
        ['capacity', 'Charge', '%'], ['voltage_now', 'Voltage', 'V'], ['current_now', 'Current', 'A'],
        ['power_now', 'Power', 'W'], ['energy_now', 'Remaining energy', 'Wh'], ['energy_full', 'Full energy', 'Wh'],
        ['charge_now', 'Remaining charge', 'Ah'], ['charge_full', 'Full charge', 'Ah'], ['temp', 'Temperature', '°C'],
        ['cycle_count', 'Charge cycles', ''], ['time_to_empty_now', 'Time to empty', 's'], ['time_to_full_now', 'Time to full', 's'],
    ];
    for (const supply of data.power_supplies) {
        power.append(element('h3', `${supply.model_name || supply.name} · ${supply.type || 'Power supply'}`));
        power.append(details([
            ['Status', supply.status || (supply.online == null ? 'Unavailable' : supply.online ? 'Online' : 'Offline')],
            ...(supply.health ? [['Health', supply.health]] : []),
            ...supplyFields.filter(([key]) => supply[key] != null).map(([key, name, unit]) => [name, measured(supply[key], unit, ['cycle_count', 'capacity'].includes(key) ? 0 : 2)]),
        ]));
    }
    if (data.power_rails.length) {
        const rails = element('details'); rails.append(element('summary', 'Raspberry Pi PMIC measurements'));
        rails.append(details(data.power_rails.map((rail) => [rail.name, measured(rail.value, rail.unit, 3)])));
        rails.append(element('p', 'PMIC readings do not include every 5 V peripheral load.', 'footnote')); power.append(rails);
    }
    $('network-status').replaceChildren(details([
        ['System time', dateTime(data.updated_at)], ['Network time synchronized', data.clock_synchronized === 'yes' ? 'Yes' : data.clock_synchronized === 'no' ? 'No' : 'Unavailable'],
        ...data.interfaces.flatMap((network) => [[network.name, `${network.state} · ${network.addresses.join(', ') || 'No IP address'}`], [`${network.name} received / sent`, `${bytes(network.received_bytes)} / ${bytes(network.transmitted_bytes)}`]]),
    ]));
    const disks = $('disk-status'); disks.replaceChildren();
    for (const disk of data.storage) {
        const card = element('div', null, 'storage-card'); card.append(element('h3', disk.path));
        if (disk.error) card.append(element('p', disk.error, 'notice error'));
        else {
            const bar = element('progress'); bar.max = 100; bar.value = disk.used_percent; bar.setAttribute('aria-label', `${disk.path} storage used`);
            card.append(bar, details([['Available', bytes(disk.free)], ['Used / total', `${bytes(disk.used)} / ${bytes(disk.total)}`], ['Usage', `${disk.used_percent}%`], ['Directory', disk.exists ? disk.writable ? 'Writable' : 'Read only' : 'Not created yet'], ['Free inodes', `${number(disk.inodes_free)} / ${number(disk.inodes_total)}`]]));
        }
        disks.append(card);
    }
    refreshed(data.updated_at);
}

let collection = null;
let clockBusy = false;
let clockAvailable = false;
function renderClock(data) {
    clockAvailable = data.available;
    $('clock-controls').disabled = clockBusy || !clockAvailable;
    $('enable-network-time').disabled = data.can_ntp === false;
    const delta = (data.timestamp * 1000 - Date.now()) / 1000;
    $('clock-status').replaceChildren(details([
        ['Device time', dateTime(data.timestamp, data.timezone || undefined)], ['Device timezone', data.timezone],
        ['Network time', enabled(data.ntp_enabled)], ['Synchronized', data.synchronized == null ? 'Unavailable' : data.synchronized ? 'Yes' : 'No'],
        ['Compared with this computer', Math.abs(delta) < 2 ? 'Within 2 seconds' : `${duration(Math.abs(delta))} ${delta > 0 ? 'ahead' : 'behind'} (approximate)`],
    ]));
    if (!data.available) $('clock-status').append(element('p', data.error || 'Device clock controls unavailable.', 'notice error'));
}
function localDateTime(date) {
    const pad = (n) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}
function enteredTime() {
    const input = $('manual-time');
    const date = new Date(input.value);
    if (!input.value || !input.checkValidity() || !Number.isFinite(date.getTime())) throw new Error('Choose a valid date and time.');
    // Browsers silently move nonexistent local times through the DST gap.
    if (localDateTime(date).slice(0, input.value.length) !== input.value) throw new Error('This local time does not exist because the clocks move forward. Choose another time.');
    return date;
}
async function changeClock(path, body, message) {
    if (clockBusy) return;
    clockBusy = true; $('clock-controls').disabled = true;
    notice('clock-error', ''); notice('clock-message', 'Applying clock change…');
    try {
        const result = await api(path, body);
        renderClock(result);
        notice('clock-message', result.available ? message : 'Change accepted, but clock status could not be read. Refresh to verify.');
    } catch (error) {
        notice('clock-message', ''); notice('clock-error', error.message);
        try { renderClock(await api('/api/clock')); } catch { /* Keep the action error visible. */ }
    } finally { clockBusy = false; $('clock-controls').disabled = !clockAvailable; }
}

function speciesColor(index, count) { return `hsl(${Math.round(index / Math.max(1, count) * 360)} 52% 36%)`; }
function dayKey(timestamp, timezone) {
    const parts = new Intl.DateTimeFormat('en-US', {timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit'}).formatToParts(new Date(timestamp * 1000));
    const part = (type) => parts.find((item) => item.type === type).value;
    return `${part('year')}-${part('month')}-${part('day')}`;
}
function svgNode(tag, attributes = {}, text) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
    if (text != null) node.textContent = text;
    return node;
}
function renderTimeline() {
    if (!collection) return;
    const day = $('timeline-day').value, species = collection.species, timezone = collection.timezone;
    $('timeline-timezone').textContent = timezone;
    if (!day) { empty($('species-timeline'), 'Recordings will appear here after collection begins.'); return; }
    const bins = new Map(species.bins.filter((bin) => dayKey(bin.timestamp, timezone) === day).map((bin) => [bin.timestamp, bin]));
    const slots = [], start = Date.parse(`${day}T00:00:00Z`) / 1000;
    for (let timestamp = start - 12 * 3600; timestamp < start + 38 * 3600; timestamp += 900) if (dayKey(timestamp, timezone) === day) slots.push(timestamp);
    const labels = species.labels.filter((label) => [...bins.values()].some((bin) => bin.species[label]));
    const left = 178, cell = 18, rowHeight = 54, top = 12, bottom = 42;
    const svg = svgNode('svg', {width: left + slots.length * cell + 15, height: top + (labels.length + 1) * rowHeight + bottom, role: 'img', 'aria-label': `15-minute species activity for ${day}, ${timezone}`});
    const peak = Math.max(1, ...[...bins.values()].map((bin) => bin.detections));
    for (let row = 0; row <= labels.length; row++) {
        const label = row ? labels[row - 1] : 'Detections', y = top + row * rowHeight;
        svg.append(svgNode('rect', {x: left, y, width: slots.length * cell, height: rowHeight - 7, fill: '#f3f6f0'}));
        svg.append(svgNode('text', {x: 0, y: y + 18, fill: '#284839', 'font-size': 12, 'font-weight': 600}, label));
        if (row && species.latin_names[label]) svg.append(svgNode('text', {x: 0, y: y + 33, fill: '#65766f', 'font-size': 10, 'font-style': 'italic'}, species.latin_names[label]));
        slots.forEach((timestamp, index) => {
            const bin = bins.get(timestamp), info = row ? bin?.species[label] : null;
            const level = row ? info?.level || 0 : (bin?.detections || 0) / peak;
            if (!level) return;
            const height = level * (rowHeight - 10), color = row ? speciesColor(species.labels.indexOf(label), species.labels.length) : '#708276';
            const bar = svgNode('rect', {x: left + index * cell, y: y + rowHeight - 7 - height, width: cell - 1, height, fill: color, opacity: row && !info.top1 ? .35 : 1});
            bar.append(svgNode('title', {}, `${dateTime(timestamp, timezone)} · ${label}: ${row ? `${info.count} qualifying top-1 recordings; strongest confidence ${(info.max_confidence * 100).toFixed(1)}%` : `${bin.detections} detections / ${bin.recordings} recordings`}`));
            svg.append(bar);
            const count = row ? info.count : bin.detections;
            if (count && (!row || (info.top1 && info.level >= .5))) svg.append(svgNode('text', {x: left + index * cell + cell / 2, y: y + rowHeight - 12, fill: '#fff', 'font-size': 9, 'text-anchor': 'middle'}, count));
        });
    }
    slots.forEach((timestamp, index) => {
        const time = new Intl.DateTimeFormat('en-US', {timeZone: timezone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23'}).format(new Date(timestamp * 1000));
        if (!time.endsWith(':00')) return;
        const x = left + index * cell;
        svg.append(svgNode('line', {x1: x, x2: x, y1: top, y2: top + (labels.length + 1) * rowHeight - 7, stroke: '#a5b3a6', 'stroke-width': .6}));
        svg.append(svgNode('text', {x: x + 2, y: top + (labels.length + 1) * rowHeight + 14, fill: '#63776b', 'font-size': 10}, time));
    });
    $('species-timeline').replaceChildren(svg);
}

function renderCollection(data) {
    collection = data;
    $('data-summary').replaceChildren(metric('TOTAL FILES', number(data.total_files), `${number(data.images)} images · ${number(data.ml_results)} ML results`), metric('WAV RECORDINGS', number(data.recordings), `${number(data.pending_files)} files in progress`), metric('COLLECTION SIZE', bytes(data.total_bytes), 'Audio, images, and metadata'), metric('SPECIES DETECTIONS', number(data.species.qualified_recordings), `${data.species.histogram.length} species with qualifying predictions`));
    const histogram = $('species-histogram'); histogram.replaceChildren();
    const maximum = Math.max(1, ...data.species.histogram.map((item) => item.count));
    for (const item of data.species.histogram) {
        const row = element('div', null, 'histogram-row'), label = element('div', item.label, 'histogram-label');
        label.append(element('small', item.name));
        const track = element('div', null, 'histogram-track'), bar = element('div', null, 'histogram-bar');
        bar.style.width = `${item.count / maximum * 100}%`; bar.style.background = speciesColor(data.species.labels.indexOf(item.label), data.species.labels.length);
        track.append(bar); row.append(label, track, element('div', number(item.count), 'histogram-count')); histogram.append(row);
    }
    if (!data.species.histogram.length) empty(histogram, 'No species predictions at or above 25% confidence yet.');
    $('species-breakdown').textContent = `${number(data.species.noise_recordings)} NOISE winners · ${number(data.species.low_confidence_recordings)} low-confidence winners · ${number(data.species.unclassified_recordings)} recordings awaiting valid ML results. These are excluded from the histogram.`;
    if (data.first_recording != null) {
        $('timeline-day').min = dayKey(data.first_recording, data.timezone); $('timeline-day').max = dayKey(data.last_recording, data.timezone);
        if (!$('timeline-day').value) $('timeline-day').value = $('timeline-day').max;
    }
    renderTimeline();
    const recent = $('recent-recordings'); const scroll = recent.scrollTop; recent.replaceChildren();
    for (const item of data.recent) {
        const card = element('article', null, 'recording-card'), link = element('a');
        link.href = item.image_url; link.target = '_blank'; link.rel = 'noopener'; link.title = 'Open full-size spectrogram';
        const image = element('img'); image.src = item.image_url; image.alt = `Spectrogram for ${item.name}`; image.loading = 'lazy';
        image.addEventListener('error', () => image.replaceWith(element('p', 'Image is no longer available. Refresh the collection.', 'notice')));
        link.append(image); card.append(link, element('h3', item.name), element('time', dateTime(item.timestamp, data.timezone)));
        const meta = [bytes(item.bytes)];
        if (item.duration_ms != null) meta.push(`${(item.duration_ms / 1000).toFixed(2)} s`);
        if (item.sample_rate != null) meta.push(`${number(item.sample_rate)} Hz`);
        if (item.window_count != null) meta.push(`${number(item.window_count)} ML windows`);
        card.append(element('p', meta.join(' · '), 'recording-meta'));
        const predictions = element('div', null, 'predictions');
        item.predictions.forEach((prediction, index) => predictions.append(element('span', `${prediction.label} ${(prediction.confidence * 100).toFixed(1)}%`, `prediction${index ? '' : ' winner'}`)));
        if (!item.predictions.length) predictions.append(element('p', item.ml_error || 'ML results pending or unavailable', 'muted'));
        card.append(predictions); recent.append(card);
    }
    if (!data.recent.length) empty(recent, 'Spectrogram images will appear here as recordings are processed.');
    recent.scrollTop = scroll;
    $('data-range').textContent = data.first_recording == null ? 'No completed WAV recordings found.' : `Collection period: ${dateTime(data.first_recording, data.timezone)} – ${dateTime(data.last_recording, data.timezone)}. Inventory refreshes every 30 seconds.`;
    notice('page-error', data.errors.length ? `Some files could not be read: ${data.errors.join(' · ')}` : '');
    refreshed(data.updated_at, data.timezone);
}

let drives = [], copyJob = null, polling = false;
const busyCopy = () => copyJob && ['estimating', 'copying'].includes(copyJob.state);
function destination() { return {drive_id: $('drive-select').value, folder: $('folder-select').value}; }
function copyControls() {
    const drive = drives.find((item) => item.id === $('drive-select').value), busy = busyCopy();
    const available = drive?.mountpoint && !drive.readonly;
    for (const id of ['drive-select', 'refresh-drives', 'new-folder']) $(id).disabled = !!busy;
    for (const id of ['folder-select', 'estimate-copy']) $(id).disabled = !!busy || !available;
    $('create-folder').disabled = !!busy || !drive?.writable;
    $('mount-drive').hidden = !drive || !!drive.mountpoint; $('mount-drive').disabled = !!busy;
    $('start-copy').hidden = !copyJob || copyJob.state !== 'ready';
    $('start-copy').disabled = !copyJob?.enough_space || destination().drive_id !== copyJob?.drive_id || destination().folder !== copyJob?.folder;
}
async function selectDrive(folder = '') {
    const drive = drives.find((item) => item.id === $('drive-select').value);
    $('folder-select').replaceChildren(new Option('Drive root', '')); copyControls();
    if (!drive) { $('drive-detail').textContent = 'Connect a USB drive to the Raspberry Pi, then refresh.'; return; }
    $('drive-detail').replaceChildren(details([['Filesystem', `${drive.filesystem} · ${drive.device}`], ['Available / total', `${bytes(drive.free)} / ${bytes(drive.total ?? drive.size)}`], ['Mount', drive.mountpoint || 'Not mounted'], ['Access', drive.readonly ? 'Read only' : drive.writable ? 'Writable' : 'Mount or grant write access on the Pi']]));
    if (drive.mountpoint && !drive.readonly) {
        const data = await api(`/api/storage/${encodeURIComponent(drive.id)}/folders`);
        for (const name of data.folders) $('folder-select').append(new Option(name, name));
        $('folder-select').value = data.folders.includes(folder) ? folder : '';
    }
    copyControls();
}
async function refreshDrives() {
    const selection = destination(), data = await api('/api/storage'); drives = data.drives;
    $('drive-select').replaceChildren(new Option(drives.length ? 'Select an external drive' : 'No external drives detected', ''));
    for (const drive of drives) $('drive-select').append(new Option(`${drive.label} · ${drive.mountpoint ? `${bytes(drive.free)} free` : `${bytes(drive.size)} · not mounted`}`, drive.id));
    if (drives.some((drive) => drive.id === selection.drive_id)) $('drive-select').value = selection.drive_id;
    else if (copyJob && drives.some((drive) => drive.id === copyJob.drive_id)) $('drive-select').value = copyJob.drive_id;
    await selectDrive(selection.drive_id ? selection.folder : copyJob?.folder || '');
    if (data.error) notice('copy-error', data.error);
}
function renderCopy(job) {
    copyJob = job;
    if (!job) { $('copy-job').hidden = true; copyControls(); return; }
    $('copy-job').hidden = false;
    const titles = {estimating: 'Calculating copy size…', ready: 'Copy estimate', copying: job.cancelling ? 'Cancelling after the current write…' : 'Copying recordings…', complete: 'Copy complete', cancelled: 'Copy cancelled', failed: 'Copy stopped'};
    $('copy-state').textContent = titles[job.state] || job.state;
    $('copy-destination').textContent = job.destination;
    $('copy-estimate').replaceChildren(details([['New files to copy', `${number(job.copy_files)} · ${bytes(job.copy_bytes)}`], ['Already copied', number(job.skipped_files)], ['Name conflicts (preserved)', number(job.conflicts)], ['Still being written', number(job.pending_files)], ['Available on drive', bytes(job.free_bytes)]]));
    const progress = $('copy-progress');
    if (job.state === 'estimating') {
        progress.removeAttribute('value'); $('copy-progress-text').textContent = `${number(job.scanned_files)} of ${number(job.total_files)} files checked. Existing copies are compared before the estimate is ready.`;
    } else {
        const percentage = job.copy_bytes ? Math.min(100, job.copied_bytes / job.copy_bytes * 100) : job.state === 'complete' ? 100 : 0;
        progress.value = percentage;
        $('copy-progress-text').textContent = job.state === 'ready' ? job.enough_space ? (job.copy_files ? 'Review this estimate, then start copying. Files created after this preview will be included in your next copy.' : 'Everything is already copied, or there are no completed files to copy.') : 'Not enough available storage. Choose another drive or free space and preview again.' : `${percentage.toFixed(1)}% · ${number(job.copied_files)} / ${number(job.copy_files)} files · ${bytes(job.copied_bytes)} / ${bytes(job.copy_bytes)}${job.bytes_per_second ? ` · ${bytes(job.bytes_per_second)}/s · about ${duration(job.eta_seconds)} remaining` : ''}`;
        if (job.state === 'complete') $('copy-progress-text').textContent += ' · Files have been flushed to the drive. Use the Pi’s safe eject command before unplugging.';
    }
    $('copy-current-file').textContent = job.current_file || '';
    $('copy-conflicts').hidden = !job.conflicts;
    $('copy-conflicts').textContent = `Existing files with different contents were preserved: ${(job.conflict_examples || []).join(', ')}${job.conflicts > 10 ? '…' : ''}. Choose another destination folder to copy these files.`;
    $('cancel-copy').hidden = !['estimating', 'copying'].includes(job.state); $('cancel-copy').disabled = !!job.cancelling;
    if (job.error) notice('copy-error', job.error);
    copyControls();
    if (busyCopy() && !polling) pollCopy();
}
async function pollCopy() {
    polling = true;
    try {
        while (busyCopy()) {
            await new Promise((resolve) => setTimeout(resolve, 1000));
            renderCopy(await api(`/api/copies/${copyJob.id}`));
        }
    } catch (error) {
        notice('copy-error', `${error.message} Reconnecting to copy progress…`);
        setTimeout(async () => {
            try { renderCopy(await api('/api/copies/current')); } catch (retryError) { notice('copy-error', `${retryError.message} Refresh this page to reconnect.`); }
        }, 3000);
    } finally { polling = false; }
}

if (document.body.dataset.page === 'status') {
    let loading = false;
    const refresh = async () => {
        if (loading) return; loading = true;
        const results = await Promise.allSettled([api('/api/status'), api('/api/clock')]);
        try {
            if (results[0].status === 'fulfilled') { renderStatus(results[0].value); notice('page-error', ''); }
            else notice('page-error', `${results[0].reason.message} Showing the last available readings.`);
            if (results[1].status === 'fulfilled') { if (!clockBusy) renderClock(results[1].value); }
            else notice('clock-error', results[1].reason.message);
        } finally { loading = false; }
    };
    $('clock-input-zone').textContent = `(${Intl.DateTimeFormat().resolvedOptions().timeZone}, this computer's timezone)`;
    $('manual-time').addEventListener('input', () => {
        try { $('clock-preview').textContent = `Will set the device to ${enteredTime().toISOString()} (UTC).`; }
        catch (error) { $('clock-preview').textContent = error.message; }
    });
    $('set-manual-time').addEventListener('click', () => {
        try { changeClock('/api/clock/manual', {instant: enteredTime().toISOString()}, 'Device time updated. Automatic network time is off.'); }
        catch (error) { notice('clock-error', error.message); }
    });
    $('set-browser-time').addEventListener('click', () => changeClock('/api/clock/manual', {instant: new Date().toISOString()}, "Device time set from this computer. Automatic network time is off."));
    $('enable-network-time').addEventListener('click', () => changeClock('/api/clock/network', {}, 'Network time enabled. Synchronization requires a reachable time server and may take a moment.'));
    action('refresh-status', refresh); refresh(); setInterval(() => { if (!document.hidden) refresh(); }, 10000);
}
if (document.body.dataset.page === 'data') {
    let loading = false;
    const refresh = async () => { if (loading) return; loading = true; try { renderCollection(await api('/api/recordings')); } catch (error) { notice('page-error', error.message); } finally { loading = false; } };
    action('refresh-data', refresh); $('timeline-day').addEventListener('change', renderTimeline);
    action('refresh-drives', refreshDrives, 'copy-error');
    $('drive-select').addEventListener('change', () => selectDrive().catch((error) => notice('copy-error', error.message)));
    $('folder-select').addEventListener('change', copyControls);
    action('mount-drive', async () => { await api(`/api/storage/${destination().drive_id}/mount`, {}); await refreshDrives(); }, 'copy-error');
    action('create-folder', async () => { const folder = $('new-folder').value; await api('/api/storage/folders', {...destination(), folder}); await selectDrive(folder); $('new-folder').value = ''; }, 'copy-error');
    action('estimate-copy', async () => { renderCopy(await api('/api/copies/estimate', destination())); }, 'copy-error');
    action('start-copy', async () => { renderCopy(await api(`/api/copies/${copyJob.id}/start`, {})); }, 'copy-error');
    action('cancel-copy', async () => { renderCopy(await api(`/api/copies/${copyJob.id}/cancel`, {})); }, 'copy-error');
    refresh();
    (async () => { try { renderCopy(await api('/api/copies/current')); await refreshDrives(); } catch (error) { notice('copy-error', error.message); } })();
    setInterval(() => { if (!document.hidden) refresh(); }, 30000);
}
