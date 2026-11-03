"""Local voice output selection, independent from browser recording playback."""
import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import audio_output_devices

router = APIRouter(prefix="/api/audio-devices")


class OutputSelection(BaseModel):
    output_device_id: str = Field(max_length=80)


@router.get("")
async def get_audio_devices():
    return await asyncio.to_thread(audio_output_devices.status)


@router.put("")
async def set_audio_devices(payload: OutputSelection):
    try:
        return await asyncio.to_thread(audio_output_devices.select_output, payload.output_device_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
