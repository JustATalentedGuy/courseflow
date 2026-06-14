export interface QuotaWindowUsage {
  used: number;
  reserved: number;
  limit: number;
  remaining: number;
  utilization_percent: number;
  resets_at: string;
}

export interface ModelQuotaUsage {
  model: string;
  requests_minute: QuotaWindowUsage;
  requests_day: QuotaWindowUsage;
  tokens_minute: QuotaWindowUsage;
  tokens_day: QuotaWindowUsage;
}

export interface QuotaUsageResponse {
  models: ModelQuotaUsage[];
}
