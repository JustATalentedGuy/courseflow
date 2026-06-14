import { request } from "./client";
import type { QuotaUsageResponse } from "../types";

export function getQuotaUsage(): Promise<QuotaUsageResponse> {
  return request<QuotaUsageResponse>("/quota/usage");
}
