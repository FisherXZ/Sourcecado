import { useCallback, useEffect, useRef, useState } from "react";

import {
  AttachmentError,
  getAttachmentLimits,
  uploadAttachment,
  type AttachmentKind,
  type AttachmentLimits,
} from "../api";
import { formatBytes, rememberAttachment } from "./attachments";

/** One file above the composer box, from the moment it is chosen. */
export type ComposerFile = {
  /** Local and stable. The id below exists only once the backend kept the file. */
  readonly key: string;
  readonly name: string;
  readonly size: number;
  readonly state: "processing" | "ready" | "failed";
  readonly id?: string;
  readonly kind?: AttachmentKind;
  /** The backend's words for a failed file. */
  readonly message?: string;
};

/**
 * The files the director is attaching to the next message.
 *
 * Each file goes to the backend as soon as it is chosen. The backend checks
 * the content, copies it into private state and reads its text. The chip shows
 * what came back. A message with a file that is still processing, or that
 * failed, cannot be sent: `blockedReason` says why.
 */
export function useComposerFiles() {
  const [files, setFiles] = useState<readonly ComposerFile[]>([]);
  const [limits, setLimits] = useState<AttachmentLimits | null>(null);
  const [notice, setNotice] = useState("");
  const current = useRef<readonly ComposerFile[]>([]);
  const known = useRef<AttachmentLimits | null>(null);
  const uploads = useRef(new Map<string, AbortController>());
  const count = useRef(0);

  const write = useCallback((next: readonly ComposerFile[]) => {
    current.current = next;
    setFiles(next);
  }, []);

  const patch = useCallback(
    (key: string, change: Partial<ComposerFile>) => {
      // A file the director already removed has no chip left to update.
      write(current.current.map((file) => (file.key === key ? { ...file, ...change } : file)));
    },
    [write],
  );

  // The backend states the limits. They are read when the menu opens and
  // before a first file is checked, and never typed into the client.
  const refreshLimits = useCallback(async (): Promise<AttachmentLimits | null> => {
    try {
      const loaded = await getAttachmentLimits();
      known.current = loaded;
      setLimits(loaded);
      return loaded;
    } catch {
      return known.current;
    }
  }, []);

  useEffect(
    () => () => {
      for (const controller of uploads.current.values()) controller.abort();
    },
    [],
  );

  const send = useCallback(
    (key: string, file: File, rules: AttachmentLimits | null) => {
      if (rules && file.size > rules.max_bytes) {
        patch(key, {
          state: "failed",
          message: `This file is ${formatBytes(file.size)}. The limit is ${formatBytes(rules.max_bytes)}.`,
        });
        return;
      }
      const controller = new AbortController();
      uploads.current.set(key, controller);
      uploadAttachment(file, file.name, controller.signal).then(
        (info) => {
          uploads.current.delete(key);
          rememberAttachment(info);
          patch(key, {
            state: "ready",
            id: info.id,
            kind: info.kind,
            name: info.name,
            size: info.size,
            message: undefined,
          });
        },
        (error: unknown) => {
          uploads.current.delete(key);
          if (error instanceof DOMException && error.name === "AbortError") return;
          patch(key, {
            state: "failed",
            message:
              error instanceof AttachmentError
                ? error.message
                : "This file could not be attached.",
          });
        },
      );
    },
    [patch],
  );

  const add = useCallback(
    async (picked: readonly File[]) => {
      if (picked.length === 0) return;
      const before = current.current.length;
      // The chips appear at once, as "processing", before the limits are
      // known. A message cannot be sent in the gap between the choice and the
      // first answer from the backend.
      const entries = picked.map<ComposerFile>((file) => {
        count.current += 1;
        return {
          key: `file-${count.current}`,
          name: file.name,
          size: file.size,
          state: "processing",
        };
      });
      write([...current.current, ...entries]);
      const rules = known.current ?? (await refreshLimits());
      const room = rules ? Math.max(0, rules.max_files - before) : picked.length;
      const over = new Set(entries.slice(room).map((entry) => entry.key));
      if (over.size > 0 && rules) {
        write(current.current.filter((file) => !over.has(file.key)));
        setNotice(
          `A message can carry up to ${rules.max_files} files. ${over.size} not attached.`,
        );
      } else {
        setNotice("");
      }
      entries.forEach((entry, index) => {
        // A file the director removed while the limits loaded is not sent.
        if (over.has(entry.key) || !current.current.some((file) => file.key === entry.key)) return;
        send(entry.key, picked[index]!, rules);
      });
    },
    [refreshLimits, send, write],
  );

  const remove = useCallback(
    (key: string) => {
      uploads.current.get(key)?.abort();
      uploads.current.delete(key);
      setNotice("");
      write(current.current.filter((file) => file.key !== key));
    },
    [write],
  );

  /** After a send: the files belong to the message now. */
  const clear = useCallback(() => {
    setNotice("");
    write([]);
  }, [write]);

  const failed = files.filter((file) => file.state === "failed").length;
  const processing = files.filter((file) => file.state === "processing").length;
  const blockedReason =
    failed > 0
      ? failed === 1
        ? "Remove the file that failed before you send."
        : `Remove the ${failed} files that failed before you send.`
      : processing > 0
        ? processing === 1
          ? "Wait for the file to finish processing."
          : `Wait for ${processing} files to finish processing.`
        : null;

  return {
    files,
    limits,
    notice,
    blockedReason,
    readyIds: files.flatMap((file) => (file.state === "ready" && file.id ? [file.id] : [])),
    add,
    remove,
    clear,
    refreshLimits,
  };
}
