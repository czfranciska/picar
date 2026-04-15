(() => {
    const vid = document.getElementById('video');
    const wsurl = document.getElementById('wsurl');
    const connectBtn = document.getElementById('connect');
    const disconnectBtn = document.getElementById('disconnect');
    const dot = document.getElementById('dot');
    const stat = document.getElementById('stat');
    const sbar = document.getElementById('sbar'),
        sfill = document.getElementById('sfill'),
        sval = document.getElementById('sval');
    const tbar = document.getElementById('tbar'),
        tfill = document.getElementById('tfill'),
        tval = document.getElementById('tval');
    const stepS = document.getElementById('stepS'),
        stepT = document.getElementById('stepT'),
        decayIn = document.getElementById('decay');
    const rateEl = document.getElementById('rate');
    const sensorContainer = document.getElementById('sensor_container');

    wsurl.value = wsurl.value || APP_CONFIG.BACKEND_URL;

    if (stepS) stepS.value = APP_CONFIG.DEFAULT_STEER_STEP;
    if (stepT) stepT.value = APP_CONFIG.DEFAULT_THROTTLE_STEP;
    if (decayIn) decayIn.value = APP_CONFIG.DEFAULT_DECAY;

    let ws = null,
        pc = null;
    let steer = 0.0,
        throttle = 0.0;
    let sendCount = 0,
        lastRate = performance.now();

    // Clamp x to [lo, hi]
    const clamp = (x, lo, hi) => x < lo ? lo : (x > hi ? hi : x);
    // Set connection status: ok=true (green), false (red), null (gray)
    const setStatus = (ok, text) => {
        dot.className = 'dot ' + (ok === true ? 'ok' : ok === false ? 'err' : '');
        stat.textContent = text;
    };

    // Update the steering/throttle bars based on current values
    function updateBars() {
        sval.textContent = steer.toFixed(2);
        tval.textContent = throttle.toFixed(2);
        const sw = Math.abs(steer) * 50;
        sfill.style.left = (steer >= 0 ? 50 : 50 - sw) + '%';
        sfill.style.width = sw + '%';
        sbar.classList.toggle('neg', steer < 0);
        const tw = Math.abs(throttle) * 50;
        tfill.style.left = (throttle >= 0 ? 50 : 50 - tw) + '%';
        tfill.style.width = tw + '%';
        tbar.classList.toggle('neg', throttle < 0);
    }

    // Send the current control values to the server
    function sendControl() {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        ws.send(JSON.stringify({
            type: 'control',
            steer,
            throttle
        }));
        sendCount++;
    }

    // Send the list of subscribed sensors to the server
    function sendSubscriptions() {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        const requestedSensors = [];

        // Find all checked sensor checkboxes and add their values to the requestedSensors list
        const checkboxes = sensorContainer.querySelectorAll('input[type="checkbox"]');
        checkboxes.forEach(cb => {
            if (cb.checked) requestedSensors.push(cb.value);
        });

        ws.send(JSON.stringify({
            type: 'subscribe_sensors',
            sensors: requestedSensors
        }));
    }

    // Request the list of available sensors from the server
    function discoverSensors() {
        if (ws && ws.readyState === WebSocket.OPEN) {
            // Notice this matches the "get_sensor_list" that pi_server.py is looking for!
            ws.send(JSON.stringify({
                type: 'get_sensor_list'
            }));
        }
    }

    // Start the WebRTC connection and set up event handlers
    async function startWebRTC() {
        pc = new RTCPeerConnection({
            iceServers: [{
                urls: ['stun:stun.l.google.com:19302']
            }]
        });
        pc.ontrack = (ev) => {
            if (ev.streams && ev.streams[0]) vid.srcObject = ev.streams[0];
        };
        pc.addTransceiver('video', {
            direction: 'recvonly'
        });
        pc.onicecandidate = (ev) => {
            if (ev.candidate && ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({
                    type: 'webrtc_ice',
                    candidate: ev.candidate
                }));
            }
        };
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        ws.send(JSON.stringify({
            type: 'webrtc_offer',
            sdp: offer.sdp
        }));
    }

    function connect() {
        if (ws && ws.readyState === WebSocket.OPEN) return;
        try {
            ws = new WebSocket(wsurl.value);
        } catch (e) {
            setStatus(false, 'invalid URL');
            return;
        }
        setStatus(null, 'connecting…');
        ws.onopen = () => {
            setStatus(true, 'connected');
            ws.send(JSON.stringify({
                role: 'client'
            }));
            startWebRTC();
            discoverSensors();
        };
        ws.onclose = () => {
            setStatus(false, 'disconnected');
            if (pc) {
                pc.close();
                pc = null;
            }
        };
        ws.onerror = () => {
            setStatus(false, 'error');
        };
        ws.onmessage = async (evt) => {
            let msg = null;
            try {
                msg = JSON.parse(evt.data);
            } catch (e) {
                return;
            }
            if (msg.type === 'webrtc_answer' && pc) {
                await pc.setRemoteDescription({
                    type: 'answer',
                    sdp: msg.sdp
                });
            } else if (msg.type === 'webrtc_ice' && pc) {
                const c = msg.candidate || {};
                try {
                    await pc.addIceCandidate(c);
                } catch (e) {}
            } else if (msg.type === 'sensor_list') {
                sensorContainer.innerHTML = '';
                // Create a checkbox for each sensor and add it to the container
                msg.sensors.forEach(sensorName => {
                    const lbl = document.createElement('label');
                    lbl.style.cssText = "font-size:14px; display:flex; align-items:center; gap:8px; margin-top:8px;";

                    lbl.innerHTML = `<input type="checkbox" value="${sensorName}" checked> ${sensorName}: <span id="val_${sensorName}" style="margin-left:auto; font-weight:bold;">--</span>`;
                    const cb = lbl.querySelector('input');
                    cb.addEventListener('change', sendSubscriptions);

                    sensorContainer.appendChild(lbl);
                });

                // Automatically subscribe to the default checked sensors
                sendSubscriptions();
            } else if (msg.type === 'sensor') {
                // Loop through all sensor data in the message
                for (const [sensorName, sensorData] of Object.entries(msg.data)) {
                    const valEl = document.getElementById(`val_${sensorName}`);
                    if (valEl) {
                        // Get the formatting configuration for this sensor, if it exists
                        const format = APP_CONFIG.SENSOR_FORMATS[sensorName] || {};

                        let rawValue;
                        if (format.data && sensorData[format.data] !== undefined) {
                            rawValue = sensorData[format.data];
                        } else {
                            rawValue = Object.values(sensorData)[0];
                        }
                        if (typeof rawValue === 'number') {
                            const decimals = format.decimals !== undefined ? format.decimals : 1;
                            const suffix = format.suffix || "";
                            valEl.textContent = rawValue.toFixed(decimals) + suffix;
                        } else {
                            valEl.textContent = rawValue;
                        }
                    }
                }
            }
        };
    }

    function disconnect() {
        if (ws) {
            ws.close();
            ws = null;
        }
        if (pc) {
            pc.close();
            pc = null;
        }
        setStatus(false, 'disconnected');
    }

    connectBtn.onclick = connect;
    disconnectBtn.onclick = disconnect;

    // Handle keyboard input for controlling the car
    const keys = new Set();
    window.addEventListener('keydown', (e) => {
        if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', ' '].includes(e.key)) e.preventDefault();
        if (e.repeat) return;
        keys.add(e.key);
        if (e.key.toLowerCase() === 'c') steer = 0.0;
        if (e.key.toLowerCase() === 'x') {
            steer = 0.0;
            throttle = 0.0;
        }
        if (e.key === ' ') throttle = 0.0;
        if (e.key.toLowerCase() === 'q') disconnect();
    });
    window.addEventListener('keyup', (e) => {
        keys.delete(e.key);
    });
    // Main loop: apply input, update UI, send control packets, and measure rate
    function tick(ts) {
        const sStep = parseFloat(stepS.value || '0.06'); // how fast to re-center steering
        const tStep = parseFloat(stepT.value || '0.06'); // how fast to return throttle to neutral
        const decay = clamp(parseFloat(decayIn.value || '0.00'), 0, 1); // optional extra smoothing

        const steerLeft = keys.has('ArrowLeft') || keys.has('a') || keys.has('A');
        const steerRight = keys.has('ArrowRight') || keys.has('d') || keys.has('D');
        const throttleUp = keys.has('ArrowUp') || keys.has('w') || keys.has('W');
        const throttleDn = keys.has('ArrowDown') || keys.has('s') || keys.has('S');

        // Update steer/throttle based on keys pressed
        if (steerLeft) steer -= sStep;
        if (steerRight) steer += sStep;
        if (throttleUp) throttle += tStep;
        if (throttleDn) throttle -= tStep;

        // If no steering keys are pressed, steer returns to neutral
        if (!steerLeft && !steerRight) {
            if (Math.abs(steer) <= sStep) steer = 0;
            else steer += (steer > 0 ? -sStep : sStep);
        }

        // If no throttle keys are pressed, throttle returns to neutral
        if (!throttleUp && !throttleDn) {
            if (Math.abs(throttle) <= tStep) throttle = 0;
            else throttle += (throttle > 0 ? -tStep : tStep);
        }

        // Apply optional decay for extra smoothing
        if (decay > 0) {
            steer *= (1 - decay);
            throttle *= (1 - decay);
        }

        // Clamp values to [-1, 1] and update the UI
        steer = clamp(steer, -1, 1);
        throttle = clamp(throttle, -1, 1);
        updateBars();

        sendControl();

        // If more than 1 second has passed, update the send rate display
        if (ts - lastRate > 1000) {
            rateEl.textContent = String(sendCount);
            sendCount = 0;
            lastRate = ts;
        }
        requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
})();