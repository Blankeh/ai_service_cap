from typing import Literal, Optional
from pydantic import BaseModel, Field

# Status enums — must match the values the Cloudflare worker accepts.
CameraStatus = Literal["ACTIVE", "INACTIVE", "ERROR"]
BusStatus    = Literal["RUNNING", "STOPPED", "MAINTENANCE"]


class OccupancyPayload(BaseModel):
    cameraId:       str
    busId:          Optional[int]
    cameraStatus:   CameraStatus
    busStatus:      BusStatus
    timestamp:      str           # ISO 8601 with tz
    passengerCount: int           = Field(ge=0, le=500)


class UploadResponse(BaseModel):
    device_id: str
    bus_id:    str
    bucket:    int
    received:  list[str]
    timestamp: str
