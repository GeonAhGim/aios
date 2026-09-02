// src/services/alert_service.py, src/api/schemas/alerts.py 1:1 대응.

export interface AlertCreateRequest {
  exchange: string;
  symbol: string;
  timeframe?: string;
  indicator: string;
  params?: Record<string, number>;
  operator: "<" | ">" | "<=" | ">=" | "==" | "crosses_above" | "crosses_below";
  threshold: number;
}

export interface PriceAlert {
  id: number;
  userId: string;
  exchange: string;
  symbol: string;
  timeframe: string;
  indicator: string;
  params: Record<string, number>;
  operator: string;
  threshold: number;
  status: "ACTIVE" | "TRIGGERED" | "CANCELLED";
  createdAt: string;
  triggeredAt: string | null;
  triggeredValue: number | null;
}
