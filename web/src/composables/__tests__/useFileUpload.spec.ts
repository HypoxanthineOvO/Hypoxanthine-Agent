import { ref } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Attachment } from "@/types/message";

import {
  MAX_ATTACHMENTS_PER_MESSAGE,
  useFileUpload,
} from "../useFileUpload";

const createNotification = () => ({
  error: vi.fn(),
  warning: vi.fn(),
});

describe("useFileUpload", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("uploads files and appends pending attachments", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          attachments: [
            {
              type: "file",
              url: "/tmp/report.pdf",
              filename: "report.pdf",
            } satisfies Attachment,
          ],
        }),
      }),
    );

    const fileInputRef = ref<HTMLInputElement | null>(null);
    const notification = createNotification();
    const uploader = useFileUpload({
      apiBase: "http://localhost:8765/api",
      token: "secret",
      fileInputRef,
      notification,
    });

    await uploader.uploadFiles([new File(["demo"], "report.pdf", { type: "application/pdf" })]);

    expect(uploader.pendingAttachments.value).toHaveLength(1);
    expect(uploader.pendingAttachments.value[0]?.filename).toBe("report.pdf");
  });

  it("prevents uploads beyond attachment limit", async () => {
    const notification = createNotification();
    const uploader = useFileUpload({
      apiBase: "http://localhost:8765/api",
      token: "secret",
      notification,
    });

    uploader.pendingAttachments.value = Array.from({ length: MAX_ATTACHMENTS_PER_MESSAGE }, (_, index) => ({
      type: "file",
      url: `/tmp/${index}.txt`,
    }));

    await uploader.uploadFiles([new File(["demo"], "extra.txt", { type: "text/plain" })]);

    expect(notification.warning).toHaveBeenCalledOnce();
  });

  it("reports upload failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
      }),
    );

    const notification = createNotification();
    const uploader = useFileUpload({
      apiBase: "http://localhost:8765/api",
      token: "secret",
      notification,
    });

    await uploader.uploadFiles([new File(["demo"], "broken.txt", { type: "text/plain" })]);

    expect(notification.error).toHaveBeenCalledOnce();
  });

  it("uploads files from drag and drop events", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          attachments: [{ type: "image", url: "/tmp/cat.png", filename: "cat.png" }],
        }),
      }),
    );

    const uploader = useFileUpload({
      apiBase: "http://localhost:8765/api",
      token: "secret",
      notification: createNotification(),
    });
    const preventDefault = vi.fn();
    const file = new File(["cat"], "cat.png", { type: "image/png" });
    const dropEvent = {
      preventDefault,
      dataTransfer: { files: [file] },
    } as unknown as DragEvent;

    await uploader.onComposerDrop(dropEvent);

    expect(preventDefault).toHaveBeenCalledOnce();
    expect(uploader.pendingAttachments.value[0]?.filename).toBe("cat.png");
  });
});
