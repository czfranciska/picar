(() => {
  const vid = document.getElementById('video');
  const wsurl = document.getElementById('wsurl');
  const connectBtn = document.getElementById('connect');
  const disconnectBtn = document.getElementById('disconnect');
  const dot = document.getElementById('dot'); const stat = document.getElementById('stat');
  const sbar = document.getElementById('sbar'), sfill = document.getElementById('sfill'), sval = document.getElementById('sval');
  const tbar = document.getElementById('tbar'), tfill = document.getElementById('tfill'), tval = document.getElementById('tval');
  const stepS = document.getElementById('stepS'), stepT = document.getElementById('stepT'), decayIn = document.getElementById('decay');
  const rateEl = document.getElementById('rate');

  let ws = null, pc = null;
  let steer = 0.0, throttle = 0.0;
  let sendCount = 0, lastRate = performance.now();

  const clamp = (x, lo, hi) => x < lo ? lo : (x > hi ? hi : x);
  const setStatus = (ok, text) => { dot.className='dot ' + (ok===true?'ok':ok===false?'err':''); stat.textContent=text; };

  function updateBars() {
    sval.textContent = steer.toFixed(2);
    tval.textContent = throttle.toFixed(2);
    const sw = Math.abs(steer)*50; sfill.style.left = (steer>=0?50:50-sw)+'%'; sfill.style.width = sw+'%'; sbar.classList.toggle('neg', steer<0);
    const tw = Math.abs(throttle)*50; tfill.style.left = (throttle>=0?50:50-tw)+'%'; tfill.style.width = tw+'%'; tbar.classList.toggle('neg', throttle<0);
  }

  function sendControl() {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({type:'control', steer, throttle}));
    sendCount++;
  }

  async function startWebRTC() {
    pc = new RTCPeerConnection({iceServers: [{urls: ['stun:stun.l.google.com:19302']}]});
    pc.ontrack = (ev) => { if (ev.streams && ev.streams[0]) vid.srcObject = ev.streams[0]; };
    pc.addTransceiver('video', {direction: 'recvonly'});
    pc.onicecandidate = (ev) => {
      if (ev.candidate && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({type:'webrtc_ice', candidate: ev.candidate}));
      }
    };
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    ws.send(JSON.stringify({type:'webrtc_offer', sdp: offer.sdp}));
  }

  function connect() {
    if (ws && ws.readyState === WebSocket.OPEN) return;
    try { ws = new WebSocket(wsurl.value); } catch(e) { setStatus(false,'invalid URL'); return; }
    setStatus(null, 'connecting…');
    ws.onopen = () => {
      setStatus(true,'connected');
      ws.send(JSON.stringify({role: 'client'}));
      startWebRTC();
    };
    ws.onclose = () => { setStatus(false,'disconnected'); if (pc) { pc.close(); pc=null; } };
    ws.onerror = () => { setStatus(false,'error'); };
    ws.onmessage = async (evt) => {
      let msg = null;
      try { msg = JSON.parse(evt.data); } catch(e) { return; }
      if (msg.type === 'webrtc_answer' && pc) {
        await pc.setRemoteDescription({type:'answer', sdp: msg.sdp});
      } else if (msg.type === 'webrtc_ice' && pc) {
        const c = msg.candidate || {};
        try { await pc.addIceCandidate(c); } catch(e) {}
      } else if (msg.type === 'sensor') {
        if (msg.data && msg.data.cpu_core) {
          document.getElementById('cpu_val').textContent = msg.data.cpu_core.usage_percent.toFixed(1);
        }
      }
    };
  }
  function disconnect() { if (ws) { ws.close(); ws=null; } if (pc) { pc.close(); pc=null; } setStatus(false,'disconnected'); }

  connectBtn.onclick = connect; disconnectBtn.onclick = disconnect;

  const keys = new Set();
  window.addEventListener('keydown', (e) => {
    if (['ArrowUp','ArrowDown','ArrowLeft','ArrowRight',' '].includes(e.key)) e.preventDefault();
    if (e.repeat) return; keys.add(e.key);
    if (e.key.toLowerCase()==='c') steer=0.0;
    if (e.key.toLowerCase()==='x') { steer=0.0; throttle=0.0; }
    if (e.key===' ') throttle=0.0;
    if (e.key.toLowerCase()==='q') disconnect();
  });
  window.addEventListener('keyup', (e) => { keys.delete(e.key); });

  function tick(ts) {
  const sStep = parseFloat(stepS.value||'0.06');   // how fast to re-center steering
  const tStep = parseFloat(stepT.value||'0.06');   // how fast to return throttle to neutral
  const decay = clamp(parseFloat(decayIn.value||'0.00'), 0, 1); // optional extra smoothing

  const steerLeft  = keys.has('ArrowLeft') || keys.has('a') || keys.has('A');
  const steerRight = keys.has('ArrowRight')|| keys.has('d') || keys.has('D');
  const throttleUp = keys.has('ArrowUp')   || keys.has('w') || keys.has('W');
  const throttleDn = keys.has('ArrowDown') || keys.has('s') || keys.has('S');

  // 1) Apply active inputs
  if (steerLeft)  steer  -= sStep;
  if (steerRight) steer  += sStep;
  if (throttleUp) throttle += tStep;
  if (throttleDn) throttle -= tStep;

  // 2) If no steering key is pressed, steer returns to center
  if (!steerLeft && !steerRight) {
    if (Math.abs(steer) <= sStep) steer = 0;
    else steer += (steer > 0 ? -sStep : sStep);
  }

  // 3) If no throttle key is pressed, throttle returns to neutral
  if (!throttleUp && !throttleDn) {
    if (Math.abs(throttle) <= tStep) throttle = 0;
    else throttle += (throttle > 0 ? -tStep : tStep);
  }

  // 4) Optional extra smoothing (kept from your UI)
  if (decay > 0) { steer *= (1 - decay); throttle *= (1 - decay); }

  // Clamp & update UI
  steer = clamp(steer, -1, 1);
  throttle = clamp(throttle, -1, 1);
  updateBars();

  // Send control packet
  sendControl();

  // Simple rate meter
  if (ts - lastRate > 1000) { rateEl.textContent = String(sendCount); sendCount = 0; lastRate = ts; }

  requestAnimationFrame(tick);
}

function tick(ts) {
  const sStep = parseFloat(stepS.value||'0.06');   // how fast to re-center steering
  const tStep = parseFloat(stepT.value||'0.06');   // how fast to return throttle to neutral
  const decay = clamp(parseFloat(decayIn.value||'0.00'), 0, 1); // optional extra smoothing

  const steerLeft  = keys.has('ArrowLeft') || keys.has('a') || keys.has('A');
  const steerRight = keys.has('ArrowRight')|| keys.has('d') || keys.has('D');
  const throttleUp = keys.has('ArrowUp')   || keys.has('w') || keys.has('W');
  const throttleDn = keys.has('ArrowDown') || keys.has('s') || keys.has('S');

  // 1) Apply active inputs
  if (steerLeft)  steer  -= sStep;
  if (steerRight) steer  += sStep;
  if (throttleUp) throttle += tStep;
  if (throttleDn) throttle -= tStep;

  // 2) If no steering key is pressed, steer returns to center
  if (!steerLeft && !steerRight) {
    if (Math.abs(steer) <= sStep) steer = 0;
    else steer += (steer > 0 ? -sStep : sStep);
  }

  // 3) If no throttle key is pressed, throttle returns to neutral
  if (!throttleUp && !throttleDn) {
    if (Math.abs(throttle) <= tStep) throttle = 0;
    else throttle += (throttle > 0 ? -tStep : tStep);
  }

  // 4) Optional extra smoothing (kept from your UI)
  if (decay > 0) { steer *= (1 - decay); throttle *= (1 - decay); }

  // Clamp & update UI
  steer = clamp(steer, -1, 1);
  throttle = clamp(throttle, -1, 1);
  updateBars();

  // Send control packet
  sendControl();

  // Simple rate meter
  if (ts - lastRate > 1000) { rateEl.textContent = String(sendCount); sendCount = 0; lastRate = ts; }

  requestAnimationFrame(tick);
}
  requestAnimationFrame(tick);
})();