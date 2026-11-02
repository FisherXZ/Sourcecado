import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import { getAttachmentImage, getAttachmentText, type AttachmentInfo } from "../api";
import { FitIcon, ZoomInIcon, ZoomOutIcon } from "./AttachmentIcons";
import { describeAttachment, parseCsv } from "./attachments";
import { RetryIcon } from "./icons";

type Load<T> =
  | { readonly status: "loading" }
  | { readonly status: "ready"; readonly value: T }
  | { readonly status: "unavailable" }
  | { readonly status: "failed" };

function useLoaded<T>(load: () => Promise<T | null>, key: string) {
  const [state, setState] = useState<Load<T>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    load().then(
      (value) => {
        if (live) setState(value === null ? { status: "unavailable" } : { status: "ready", value });
      },
      () => {
        if (live) setState({ status: "failed" });
      },
    );
    return () => {
      live = false;
    };
    // `key` names the file. The loader is a new function on every render.
  }, [key, attempt]);
  return { state, retry: useCallback(() => setAttempt((count) => count + 1), []) };
}

function Gone() {
  return (
    <p className="sourcecado-attachment-gone" role="alert">
      File unavailable. It is no longer on this computer.
    </p>
  );
}

function Wait<T>({
  state,
  retry,
  children,
}: {
  readonly state: Load<T>;
  readonly retry: () => void;
  readonly children: (value: T) => ReactNode;
}) {
  if (state.status === "loading") {
    return <div className="sourcecado-inspector-skeleton" aria-label="Loading file" />;
  }
  if (state.status === "unavailable") return <Gone />;
  if (state.status === "failed") {
    return (
      <div className="sourcecado-attachment-gone" role="alert">
        <p>The file did not load.</p>
        <button type="button" className="sourcecado-attachment-retry" onClick={retry}>
          <RetryIcon />
          Try again
        </button>
      </div>
    );
  }
  return <>{children(state.value)}</>;
}

function cutNote(info: AttachmentInfo, shown: number): string {
  return info.total_chars !== null
    ? `Showing the first ${shown.toLocaleString("en-US")} of ${info.total_chars.toLocaleString("en-US")} characters. The model reads the same part.`
    : `Showing the first ${shown.toLocaleString("en-US")} characters. The rest was not read. The model reads the same part.`;
}

function TextView({ info }: { readonly info: AttachmentInfo }) {
  const { state, retry } = useLoaded(() => getAttachmentText(info.id), info.id);
  return (
    <Wait state={state} retry={retry}>
      {(loaded) => (
        <>
          {info.kind === "pdf" ? (
            <p className="sourcecado-attachment-note">
              Text read from the PDF. Layout and pictures are not shown.
            </p>
          ) : null}
          {loaded.truncated ? (
            <p className="sourcecado-attachment-note" data-flag="truncated">
              {cutNote(info, loaded.text.length)}
            </p>
          ) : null}
          <pre
            className="sourcecado-attachment-text-body"
            tabIndex={0}
            aria-label={`Text of ${info.name}`}
          >
            {loaded.text}
          </pre>
        </>
      )}
    </Wait>
  );
}

function CsvView({ info }: { readonly info: AttachmentInfo }) {
  const { state, retry } = useLoaded(() => getAttachmentText(info.id), info.id);
  return (
    <Wait state={state} retry={retry}>
      {(loaded) => <CsvTable info={info} text={loaded.text} truncated={loaded.truncated} />}
    </Wait>
  );
}

function CsvTable({
  info,
  text,
  truncated,
}: {
  readonly info: AttachmentInfo;
  readonly text: string;
  readonly truncated: boolean;
}) {
  const rows = useMemo(() => {
    const parsed = parseCsv(text);
    // The cut can land inside a row. A half row would read as a real one.
    return truncated ? parsed.slice(0, -1) : parsed;
  }, [text, truncated]);
  const width = Math.max(1, ...rows.map((row) => row.length));
  const [head, ...body] = rows;
  const cell = (row: readonly string[], at: number) => row[at] ?? "";
  return (
    <>
      <p className="sourcecado-attachment-note">
        {body.length} {body.length === 1 ? "row" : "rows"} under the header row.
      </p>
      {truncated ? (
        <p className="sourcecado-attachment-note" data-flag="truncated">
          {cutNote(info, text.length)}
        </p>
      ) : null}
      {head ? (
        <div
          className="sourcecado-attachment-table"
          role="region"
          aria-label={`Rows of ${info.name}`}
          tabIndex={0}
        >
          <table>
            <thead>
              <tr>
                {Array.from({ length: width }, (_, at) => (
                  <th key={at} scope="col">
                    {cell(head, at)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {body.map((row, index) => (
                <tr key={index}>
                  {Array.from({ length: width }, (_, at) => (
                    <td key={at}>{cell(row, at)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="sourcecado-inspector-empty">No rows to show.</p>
      )}
    </>
  );
}

const ZOOM_STEPS = [0.5, 0.75, 1, 1.5, 2, 3, 4] as const;
const FIT_STEP = 2;

function ImageView({ info }: { readonly info: AttachmentInfo }) {
  const { state, retry } = useLoaded(() => getAttachmentImage(info.id), info.id);
  const [step, setStep] = useState(FIT_STEP);
  const [fit, setFit] = useState<number | null>(null);
  const frame = useRef<HTMLDivElement>(null);
  const image = useRef<HTMLImageElement>(null);
  const scale = ZOOM_STEPS[step]!;

  // "Fit" is the picture at its own size, or the panel's width if it is wider.
  const measure = useCallback(() => {
    const natural = image.current?.naturalWidth ?? 0;
    const room = frame.current?.clientWidth ?? 0;
    setFit(natural > 0 && room > 0 ? Math.min(natural, room) : null);
  }, []);
  useEffect(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure]);

  const zoom = (next: number) =>
    setStep(Math.min(ZOOM_STEPS.length - 1, Math.max(0, next)));
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "+" || event.key === "=") zoom(step + 1);
    else if (event.key === "-" || event.key === "_") zoom(step - 1);
    else if (event.key === "0") zoom(FIT_STEP);
    else return;
    event.preventDefault();
  };

  return (
    <Wait state={state} retry={retry}>
      {(source) => (
        <>
          <div className="sourcecado-attachment-zoom" role="group" aria-label="Zoom">
            <button
              type="button"
              aria-label="Zoom out"
              title="Zoom out"
              disabled={step === 0}
              onClick={() => zoom(step - 1)}
            >
              <ZoomOutIcon />
            </button>
            <span className="sourcecado-attachment-zoom-level" role="status">
              {Math.round(scale * 100)}%
            </span>
            <button
              type="button"
              aria-label="Zoom in"
              title="Zoom in"
              disabled={step === ZOOM_STEPS.length - 1}
              onClick={() => zoom(step + 1)}
            >
              <ZoomInIcon />
            </button>
            <button
              type="button"
              aria-label="Fit to panel"
              title="Fit to panel"
              disabled={step === FIT_STEP}
              onClick={() => zoom(FIT_STEP)}
            >
              <FitIcon />
            </button>
          </div>
          <div
            ref={frame}
            className="sourcecado-attachment-image"
            tabIndex={0}
            aria-label={`Picture ${info.name}. Press plus or minus to zoom.`}
            onKeyDown={onKeyDown}
          >
            <img
              ref={image}
              src={source}
              alt={info.name}
              onLoad={measure}
              style={
                fit !== null
                  ? { width: `${Math.round(fit * scale)}px`, maxWidth: "none" }
                  : { width: `${scale * 100}%`, maxWidth: "none" }
              }
            />
          </div>
        </>
      )}
    </Wait>
  );
}

/**
 * The inspector's view of an attached file. PDF, text and Markdown show as
 * readable text, an image shows with zoom, and a CSV shows as a table. Every
 * view says when the text is cut, and shows "File unavailable" when the bytes
 * are gone.
 */
export function AttachmentPreview({ info }: { readonly info: AttachmentInfo }) {
  return (
    <div className="sourcecado-attachment-preview" data-kind={info.kind}>
      <p className="sourcecado-attachment-facts">{describeAttachment(info)}</p>
      {info.kind === "image" ? (
        <ImageView info={info} />
      ) : info.kind === "csv" ? (
        <CsvView info={info} />
      ) : (
        <TextView info={info} />
      )}
    </div>
  );
}
