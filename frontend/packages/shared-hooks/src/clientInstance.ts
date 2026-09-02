import { AiosApiClient } from "@aios/api-client";
import { useAuthStore } from "./useAuthStore";

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export const apiClient = new AiosApiClient(baseUrl, () => useAuthStore.getState().token);
