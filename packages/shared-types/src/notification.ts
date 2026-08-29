// src/core/notifications/history.py, src/core/notifications/preferences.py 1:1 대응.

export interface NotificationHistoryEntry {
  eventType: string;
  channel: "EMAIL" | "PUSH" | "IN_APP";
  status: "SENT" | "FAILED";
  createdAt: string;
}

export type NotificationPreferences = Record<string, boolean>;

export interface PreferenceUpdateResult {
  applied: NotificationPreferences;
  rejectedFields: string[];
}
