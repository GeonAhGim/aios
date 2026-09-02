// src/api/schemas/device_token.py, src/services/device_token_service.py 1:1 대응.

export interface DeviceTokenRegisterRequest {
  deviceToken: string;
  platform: "iOS" | "Android";
}

export interface DeviceTokenRecord {
  deviceId: number;
  registeredAt: string;
  isActive: boolean;
}
