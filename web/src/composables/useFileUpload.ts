import { ref } from "vue";
import type { Ref } from "vue";

import type { Attachment } from "@/types/message";
import { makeApiUrl, withApiToken } from "@/utils/messageRouting";

interface NotificationLike {
  error?: (options: { title: string; content: string; duration: number }) => void;
  warning?: (options: { title: string; content: string; duration: number }) => void;
}

interface UseFileUploadOptions {
  apiBase: string;
  token: string;
  notification?: NotificationLike | null;
  fileInputRef?: Ref<HTMLInputElement | null>;
}

export const MAX_ATTACHMENTS_PER_MESSAGE = 5;

const normalizeSelectedFiles = (files: FileList | File[] | null | undefined): File[] =>
  Array.from(files ?? []).filter((file) => file.size >= 0);

export function useFileUpload(options: UseFileUploadOptions) {
  const pendingAttachments = ref<Attachment[]>([]);
  const isUploadingAttachments = ref(false);
  const isComposerDragActive = ref(false);

  const uploadFiles = async (incomingFiles: File[]): Promise<void> => {
    const remainingSlots = MAX_ATTACHMENTS_PER_MESSAGE - pendingAttachments.value.length;
    if (remainingSlots <= 0) {
      options.notification?.warning?.({
        title: "附件已达上限",
        content: "每条消息最多上传 5 个附件。",
        duration: 3000,
      });
      return;
    }

    const files = incomingFiles.slice(0, remainingSlots);
    if (!files.length) {
      return;
    }

    const formData = new FormData();
    files.forEach((file) => {
      formData.append("file", file);
    });

    isUploadingAttachments.value = true;
    try {
      const response = await fetch(
        withApiToken(makeApiUrl("upload", options.apiBase), options.token),
        {
          method: "POST",
          body: formData,
        },
      );

      if (!response.ok) {
        const message = response.status === 413 ? "单个文件不能超过 100MB。" : "上传失败，请稍后重试。";
        throw new Error(message);
      }

      const payload = (await response.json()) as { attachments?: Attachment[] };
      const uploaded = Array.isArray(payload.attachments) ? payload.attachments : [];
      pendingAttachments.value = [...pendingAttachments.value, ...uploaded];
    } catch (error) {
      options.notification?.error?.({
        title: "附件上传失败",
        content: error instanceof Error ? error.message : "上传失败，请稍后重试。",
        duration: 3500,
      });
    } finally {
      isUploadingAttachments.value = false;
    }
  };

  const openFilePicker = (): void => {
    options.fileInputRef?.value?.click();
  };

  const removePendingAttachment = (index: number): void => {
    pendingAttachments.value = pendingAttachments.value.filter((_, itemIndex) => itemIndex !== index);
  };

  const onFileInputChange = async (event: Event): Promise<void> => {
    const input = event.target as HTMLInputElement | null;
    await uploadFiles(normalizeSelectedFiles(input?.files));
    if (input) {
      input.value = "";
    }
  };

  const onComposerDragOver = (event: DragEvent): void => {
    event.preventDefault();
    isComposerDragActive.value = true;
  };

  const onComposerDragLeave = (event: DragEvent): void => {
    event.preventDefault();
    isComposerDragActive.value = false;
  };

  const onComposerDrop = async (event: DragEvent): Promise<void> => {
    event.preventDefault();
    isComposerDragActive.value = false;
    await uploadFiles(normalizeSelectedFiles(event.dataTransfer?.files));
  };

  const onComposerPaste = async (event: ClipboardEvent): Promise<void> => {
    const files = normalizeSelectedFiles(event.clipboardData?.files);
    if (!files.length) {
      return;
    }

    event.preventDefault();
    await uploadFiles(files);
  };

  return {
    isComposerDragActive,
    isUploadingAttachments,
    onComposerDragLeave,
    onComposerDragOver,
    onComposerDrop,
    onComposerPaste,
    onFileInputChange,
    openFilePicker,
    pendingAttachments,
    removePendingAttachment,
    uploadFiles,
  };
}
