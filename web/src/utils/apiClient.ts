export class ApiClientError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;

  constructor(message: string, status: number, code: string, retryable: boolean) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

type ApiErrorHandler = (error: ApiClientError) => void;

let apiErrorHandler: ApiErrorHandler | null = null;

export function setApiErrorHandler(handler: ApiErrorHandler | null): void {
  apiErrorHandler = handler;
}

function buildHttpError(status: number, message: string): ApiClientError {
  const retryable = status >= 500;
  return new ApiClientError(message, status, `HTTP_${status}`, retryable);
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { message?: unknown; detail?: unknown };
    const candidate = payload.message ?? payload.detail;
    if (typeof candidate === "string" && candidate.trim().length > 0) {
      return candidate;
    }
  } catch {
    // Keep fallback below.
  }
  return `HTTP ${response.status}`;
}

function dispatchError(error: ApiClientError): void {
  if (apiErrorHandler) {
    apiErrorHandler(error);
  }
}

export async function apiGetJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    const networkError = new ApiClientError(
      "网络连接失败，请检查连接后重试",
      0,
      "NETWORK_ERROR",
      true,
    );
    dispatchError(networkError);
    throw networkError;
  }

  if (!response.ok) {
    const message = await readErrorMessage(response);
    const error = buildHttpError(response.status, message);
    dispatchError(error);
    throw error;
  }

  return (await response.json()) as T;
}
