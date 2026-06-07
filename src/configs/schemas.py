from typing import Optional
from pydantic import BaseModel, Field


class OccupancyPayload(BaseModel):
    cameraId:       str
    busId:          Optional[int]
    route:          Optional[str]
    cameraStatus:   str           # "ACTIVE" | "ERROR"
    busStatus:      str           # "RUNNING" | "STOPPED" | ...
    timestamp:      str           # ISO 8601 with tz
    passengerCount: int           = Field(ge=0, le=500)
    driverName:     Optional[str] = None


class BusEntry(BaseModel):
    busId:      int
    route:      Optional[str] = None
    busStatus:  str           = "RUNNING"
    driverName: Optional[str] = None


class UploadResponse(BaseModel):
    device_id: str
    bus_id:    str
    bucket:    int
    received:  list[str]
    timestamp: str
