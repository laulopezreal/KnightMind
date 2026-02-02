import { request } from './core';

export interface ReportRequest {
    category: 'bug' | 'feature' | 'feedback';
    description: string;
    page?: string;
    username?: string;
}

export interface ReportResponse {
    id: string;
    message: string;
}

export function submitReport(data: ReportRequest): Promise<ReportResponse> {
    return request<ReportResponse>('/reports', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
}
