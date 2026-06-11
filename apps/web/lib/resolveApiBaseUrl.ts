const DEFAULT_API_BASE_URL = 'http://localhost:8000';

type ResolveApiBaseUrlOptions = {
  isServer?: boolean;
  internalBaseUrl?: string;
  publicBaseUrl?: string;
};

export function resolveApiBaseUrl(options: ResolveApiBaseUrlOptions = {}): string {
  const publicBaseUrl =
    options.publicBaseUrl ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
  const isServer = options.isServer ?? typeof window === 'undefined';

  if (isServer) {
    return options.internalBaseUrl ?? process.env.API_INTERNAL_BASE_URL ?? publicBaseUrl;
  }

  return publicBaseUrl;
}
