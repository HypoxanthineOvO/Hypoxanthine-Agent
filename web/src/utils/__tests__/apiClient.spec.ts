import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  apiGetJson,
  setApiErrorHandler,
} from "../apiClient";

afterEach(() => {
  setApiErrorHandler(null);
  vi.restoreAllMocks();
});

describe("apiClient", () => {
  it("returns parsed json when response is ok", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ ok: true }),
      }),
    );

    const payload = await apiGetJson<{ ok: boolean }>("/api/sessions");
    expect(payload).toEqual({ ok: true });
  });

  it("throws ApiClientError for 401 and marks non-retryable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ message: "unauthorized" }),
      }),
    );

    await expect(apiGetJson("/api/sessions")).rejects.toEqual(
      expect.objectContaining({
        name: "ApiClientError",
        status: 401,
        retryable: false,
      }),
    );
  });

  it("throws ApiClientError for 5xx and marks retryable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: async () => ({ message: "busy" }),
      }),
    );

    await expect(apiGetJson("/api/sessions")).rejects.toEqual(
      expect.objectContaining({
        name: "ApiClientError",
        status: 503,
        retryable: true,
      }),
    );
  });

  it("invokes registered error handler", async () => {
    const onError = vi.fn();
    setApiErrorHandler(onError);

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => ({ message: "boom" }),
      }),
    );

    await expect(apiGetJson("/api/sessions")).rejects.toBeInstanceOf(ApiClientError);
    expect(onError).toHaveBeenCalledTimes(1);
  });
});
