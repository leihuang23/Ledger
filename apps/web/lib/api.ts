export type HealthResponse = {
  status: string;
  service: string;
  version: string;
};

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

export async function getHealth(): Promise<HealthResponse> {
  try {
    const response = await fetch(`${apiBaseUrl}/health`, {
      cache: 'no-store',
    });

    if (!response.ok) {
      throw new Error(`Health check failed with status ${response.status}`);
    }

    return response.json() as Promise<HealthResponse>;
  } catch {
    return {
      status: 'unavailable',
      service: 'ops-agent-api',
      version: 'unknown',
    };
  }
}
