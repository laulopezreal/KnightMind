import { request, ApiError } from './core';

export interface ImportResult {
    message: string;
    games_count: number;
    new_games: number;
    skipped_duplicates: number;
}

export async function getUsers(): Promise<string[]> {
    const data = await request<{ users: string[] }>('/users');
    return data.users;
}

export async function importChessComGames(username: string): Promise<ImportResult> {
    try {
        return await request<ImportResult>(`/import/chesscom?username=${encodeURIComponent(username)}`, {
            method: 'POST',
        });
    } catch (err) {
        if (err instanceof ApiError) {
            if (err.statusCode === 404) throw new ApiError('User not found', 404, err.detail);
            if (err.statusCode === 429) throw new ApiError('Rate limited by Chess.com', 429, err.detail);
            if (err.statusCode === 502) throw new ApiError('Network error connecting to Chess.com', 502, err.detail);
        }
        throw err;
    }
}

export interface ValidateUserResponse {
    valid: boolean;
    username?: string;
    error?: string;
}

export interface ImportStatusResponse {
    last_imported_at: string | null;
    last_new_games: number | null;
}

export interface UserStatus {
    username: string;
    games_count: number;
    puzzles_count: number;
    due_count: number;
    next_due_at: string | null;
    has_new_games: boolean;
}

export async function validateChessComUser(username: string): Promise<ValidateUserResponse> {
    // Keep validation requests routed through the same /api proxy for local/prod parity.
    return request<ValidateUserResponse>(`/users/validate?username=${encodeURIComponent(username)}`);
}

export async function getImportStatus(username: string): Promise<ImportStatusResponse> {
    return request<ImportStatusResponse>(`/import/status?username=${encodeURIComponent(username)}`);
}

export async function getUserStatus(username: string): Promise<UserStatus> {
    return request<UserStatus>(`/users/${encodeURIComponent(username)}/status`);
}

export interface MotifPerformance {
    name: string;
    total_puzzles: number;
    passed: number;
    accuracy: number;
    rank: 'needs_work' | 'learning' | 'mastered';
    attempts: number;
    // True when attempts is below the reliability threshold: accuracy/rank
    // are shown but should not be treated as a confident weakness signal.
    insufficient_data: boolean;
}

export interface MotifPerformanceResponse {
    motifs: MotifPerformance[];
    weakest_motifs: string[];
    total_motifs_practiced: number;
}

export async function getMotifPerformance(username: string): Promise<MotifPerformanceResponse> {
    return request<MotifPerformanceResponse>(`/users/${encodeURIComponent(username)}/motifs/performance`);
}

// Dashboard Types
export interface RecentFormData {
    last_20_results: ('pass' | 'fail')[];
    accuracy: number;
    trend: 'up' | 'down' | 'steady';
    sample_size: number;
    // True when too few reviews to read a direction (trend is forced 'steady').
    insufficient_data: boolean;
}

export interface ScheduleData {
    due_now: number;
    due_in_4h: number;
    next_review_at: string | null;
}

export interface DashboardSummary {
    username: string;
    last_session_at: string | null;
    days_since_last_session: number;
    total_sessions: number;
    training_streak_days: number;
    recent_form: RecentFormData;
    schedule: ScheduleData;
    needs_warmup: boolean;
}

export interface TrendDataPoint {
    date: string;  // ISO date string
    accuracy: number;
}

export interface MotifTrend {
    motif: string;
    start_accuracy: number;
    end_accuracy: number;
    change: number;
    trend: 'up' | 'down' | 'steady';
    total_reviews: number;
    // True when too few reviews to read a direction (trend is forced 'steady').
    insufficient_data: boolean;
    data_points: TrendDataPoint[];
}

export interface TrendsResponse {
    window_days: number;
    motif_trends: MotifTrend[];
}

export interface TrickyPuzzle {
    puzzle_id: string;
    title: string;
    fail_count: number;
    last_attempted_at: string;
}

export interface TrickyPuzzlesResponse {
    puzzles: TrickyPuzzle[];
    total_count: number;
}

// Dashboard API Functions
export async function getDashboardSummary(username: string): Promise<DashboardSummary> {
    return request<DashboardSummary>(`/users/${encodeURIComponent(username)}/dashboard`);
}

export async function getMotifTrends(username: string, windowDays: number = 30): Promise<TrendsResponse> {
    return request<TrendsResponse>(`/users/${encodeURIComponent(username)}/trends?window=${windowDays}`);
}

export async function getTrickyPuzzles(username: string, limit: number = 5): Promise<TrickyPuzzlesResponse> {
    return request<TrickyPuzzlesResponse>(`/users/${encodeURIComponent(username)}/puzzles/tricky?limit=${limit}`);
}

// --- Mistake causes (Insights) ---

export interface MistakeCause {
    cause: string;
    label: string;
    mistakes: number;
    dominant_phase?: string | null;
    verified_attempts: number;
    verified_puzzles: number;
    accuracy?: number | null;
    insufficient_data: boolean;
    is_unclassified: boolean;
}

export interface MistakeCausesResponse {
    username: string;
    causes: MistakeCause[];
    total_diagnosed: number;
    pending: number;
    min_for_ranking: number;
}

export async function getMistakeCauses(username: string): Promise<MistakeCausesResponse> {
    return await request<MistakeCausesResponse>(
        `/users/${encodeURIComponent(username)}/mistake-causes`
    );
}

export interface MistakePattern {
    cause: string;
    name: string;
    description: string;
    mistakes: number;
    recent_mistakes: number;
    dominant_phase?: string | null;
    accuracy?: number | null;
    priority: number;
}

export interface MistakePatternsResponse {
    username: string;
    patterns: MistakePattern[];
    below_threshold: number;
    pending: number;
}

export async function getMistakePatterns(
    username: string
): Promise<MistakePatternsResponse> {
    return await request<MistakePatternsResponse>(
        `/users/${encodeURIComponent(username)}/mistake-patterns`
    );
}
