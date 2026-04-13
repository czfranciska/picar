import json
from typing import Optional
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer
from aiortc.sdp import candidate_from_sdp

class WebRTCServerAV:
    def __init__(self, device: str, w: int, h: int, fps: int, input_format: str, stun: Optional[str]):
        self.device = device
        self.w, self.h, self.fps = w, h, fps
        self.input_format = input_format
        self.stun = stun
        self.pc: Optional[RTCPeerConnection] = None
        self.player: Optional[MediaPlayer] = None

    # Clean up existing WebRTC connection and media player
    async def _cleanup(self):
        if self.player:
            p = self.player
            self.player = None
            for tr in (getattr(p, "video", None), getattr(p, "audio", None)):
                try:
                    if tr and hasattr(tr, "stop"): tr.stop()
                except Exception:
                    pass
            try:
                if hasattr(p, "stop"): p.stop()
            except Exception:
                pass
            try:
                if hasattr(p, "close"): p.close()
            except Exception:
                pass
        if self.pc:
            try:
                await self.pc.close()
            except Exception:
                pass
            self.pc = None

    # Create a MediaPlayer for the camera
    def _make_player(self) -> MediaPlayer:
        opts = {"video_size": f"{self.w}x{self.h}", "framerate": str(self.fps), "input_format": self.input_format}
        return MediaPlayer(self.device, format="v4l2", options=opts)

    # Handle an incoming WebRTC offer
    async def handle_offer(self, ws, obj):
        await self._cleanup()
        ice_servers = [RTCIceServer(urls=[self.stun])] if self.stun else []
        self.pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=ice_servers))

        self.player = self._make_player()
        if not self.player.video:
            raise RuntimeError("Camera did not provide a video track")
        self.pc.addTrack(self.player.video)

        @self.pc.on("icecandidate")
        async def on_icecandidate(candidate):
            if candidate:
                await ws.send(json.dumps({
                    "type": "webrtc_ice",
                    "candidate": {"candidate": candidate.to_sdp(), "sdpMid": candidate.sdpMid,
                                  "sdpMLineIndex": candidate.sdpMLineIndex}
                }))

        offer = RTCSessionDescription(sdp=obj.get("sdp", ""), type="offer")
        await self.pc.setRemoteDescription(offer)
        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)
        await ws.send(json.dumps({"type": "webrtc_answer", "sdp": self.pc.localDescription.sdp}))

    # Handle an incoming ICE candidate from the client
    async def handle_ice(self, obj):
        if not self.pc: return
        c = obj.get("candidate", {})
        cand_line = c.get("candidate")
        if cand_line:
            cand = candidate_from_sdp(cand_line)
            cand.sdpMid = c.get("sdpMid")
            cand.sdpMLineIndex = c.get("sdpMLineIndex")
            await self.pc.addIceCandidate(cand)