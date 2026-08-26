import { useState } from "react";

import {
  cancelShellTask,
  createWorkspaceGrant,
  pickDirectory,
  revokeHostApproval,
  revokeWorkspaceGrant,
  updateWorkspaceGrant,
  type DirectoryRequest,
  type WorkspaceDiagnostics,
  type WorkspaceGrant,
  type WorkspaceGrantInput,
} from "../api";

function accessLabel(access: WorkspaceGrant["access"]): string {
  return access === "read_write" ? "Read and write" : "Read only";
}

export function WorkspaceSettings({
  workspace,
  onChange,
}: {
  readonly workspace: WorkspaceDiagnostics;
  readonly onChange: (workspace: WorkspaceDiagnostics) => void;
}) {
  const [label, setLabel] = useState("Workspace");
  const [access, setAccess] = useState<WorkspaceGrant["access"]>("read_write");
  const [allowShell, setAllowShell] = useState(true);
  const [pending, setPending] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  async function choose(input: Omit<WorkspaceGrantInput, "path">) {
    setPending(input.request_id ?? "new");
    setFailed(false);
    setFeedback(null);
    try {
      const path = await pickDirectory();
      if (!path) return;
      const response = await createWorkspaceGrant({ path, ...input });
      onChange({
        ...workspace,
        grants: [...workspace.grants, response.grant],
        directory_requests: input.request_id
          ? workspace.directory_requests.filter(
              (request) => request.id !== input.request_id,
            )
          : workspace.directory_requests,
      });
      setFeedback(`${response.grant.label} is now available to Sourcecado.`);
    } catch {
      setFailed(true);
    } finally {
      setPending(null);
    }
  }

  async function changeGrant(
    grant: WorkspaceGrant,
    changes: Partial<Pick<WorkspaceGrant, "access" | "allow_shell">>,
  ) {
    setPending(grant.id);
    setFailed(false);
    try {
      const response = await updateWorkspaceGrant(grant.id, changes);
      onChange({
        ...workspace,
        grants: workspace.grants.map((item) =>
          item.id === grant.id ? response.grant : item,
        ),
      });
    } catch {
      setFailed(true);
    } finally {
      setPending(null);
    }
  }

  async function revokeGrant(grant: WorkspaceGrant) {
    setPending(grant.id);
    setFailed(false);
    try {
      await revokeWorkspaceGrant(grant.id);
      onChange({
        ...workspace,
        grants: workspace.grants.filter((item) => item.id !== grant.id),
      });
      setFeedback(`${grant.label} access was revoked.`);
    } catch {
      setFailed(true);
    } finally {
      setPending(null);
    }
  }

  async function revokeApproval(id: string) {
    setPending(id);
    setFailed(false);
    try {
      await revokeHostApproval(id);
      onChange({
        ...workspace,
        host_approvals: workspace.host_approvals.filter(
          (approval) => approval.id !== id,
        ),
      });
    } catch {
      setFailed(true);
    } finally {
      setPending(null);
    }
  }

  async function cancelTask(id: string) {
    setPending(id);
    setFailed(false);
    try {
      const response = await cancelShellTask(id);
      onChange({
        ...workspace,
        tasks: workspace.tasks.map((task) =>
          task.task_id === id ? response.task : task,
        ),
      });
    } catch {
      setFailed(true);
    } finally {
      setPending(null);
    }
  }

  function chooseRequest(request: DirectoryRequest) {
    return choose({
      label: request.label,
      access: request.access,
      allow_shell: request.allow_shell,
      request_id: request.id,
    });
  }

  return (
    <section
      className="settings-section workspace-settings"
      aria-labelledby="workspace-access-heading"
    >
      <h2 id="workspace-access-heading">Workspace access</h2>
      <p className="workspace-runtime-status">
        <strong>
          {workspace.docker.available ? "Docker ready" : "Docker unavailable"}
        </strong>
        <span>
          {workspace.docker.available
            ? "Commands run as a restricted container user. The selected folder is mounted read-write and network access is unrestricted."
            : "Host fallback is not sandboxed. Every unmatched command requires approval."}
        </span>
      </p>
      <dl className="workspace-runtime-diagnostics">
        <div>
          <dt>Docker CLI</dt>
          <dd>{workspace.docker.cli_available ? "CLI installed" : "CLI missing"}</dd>
        </div>
        <div>
          <dt>Docker daemon</dt>
          <dd>
            {workspace.docker.daemon_available
              ? "Daemon running"
              : "Daemon unavailable"}
          </dd>
        </div>
        <div>
          <dt>Sandbox image</dt>
          <dd>{workspace.docker.image_available ? "Image ready" : "Image missing"}</dd>
        </div>
        <div>
          <dt>Container network</dt>
          <dd>Network unrestricted</dd>
        </div>
      </dl>

      {workspace.directory_requests.length > 0 ? (
        <div className="workspace-request-list">
          <h3>Requested by Sourcecado</h3>
          {workspace.directory_requests.map((request) => (
            <div key={request.id}>
              <span>{request.label}</span>
              <button
                type="button"
                disabled={pending !== null}
                onClick={() => void chooseRequest(request)}
              >
                Choose {request.label}
              </button>
            </div>
          ))}
        </div>
      ) : null}

      <div className="workspace-add-form" role="group" aria-label="Add workspace folder">
        <label>
          Workspace label
          <input value={label} onChange={(event) => setLabel(event.target.value)} />
        </label>
        <label>
          Workspace access
          <select
            value={access}
            onChange={(event) => {
              const next = event.target.value as WorkspaceGrant["access"];
              setAccess(next);
              if (next === "read_only") setAllowShell(false);
            }}
          >
            <option value="read_write">Read and write</option>
            <option value="read_only">Read only</option>
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={allowShell}
            disabled={access === "read_only"}
            onChange={(event) => setAllowShell(event.target.checked)}
          />
          Allow shell commands
        </label>
        <button
          type="button"
          disabled={pending !== null}
          onClick={() =>
            void choose({
              label: label.trim() || "Workspace",
              access,
              allow_shell: allowShell,
            })
          }
        >
          Choose folder and add
        </button>
      </div>

      <ul className="workspace-grant-list" aria-label="Active workspace grants">
        {workspace.grants.map((grant) => (
          <li key={grant.id}>
            <div>
              <strong>{grant.label}</strong>
              <span>{grant.path}</span>
              <span>{accessLabel(grant.access)}</span>
              <span>{grant.allow_shell ? "Shell enabled" : "Filesystem only"}</span>
            </div>
            <label>
              Access for {grant.label}
              <select
                value={grant.access}
                disabled={pending !== null}
                onChange={(event) => {
                  const next = event.target.value as WorkspaceGrant["access"];
                  void changeGrant(grant, {
                    access: next,
                    ...(next === "read_only" ? { allow_shell: false } : {}),
                  });
                }}
              >
                <option value="read_write">Read and write</option>
                <option value="read_only">Read only</option>
              </select>
            </label>
            <label>
              <input
                type="checkbox"
                checked={grant.allow_shell}
                disabled={pending !== null || grant.access === "read_only"}
                onChange={(event) =>
                  void changeGrant(grant, { allow_shell: event.target.checked })
                }
              />
              Shell access for {grant.label}
            </label>
            <button
              type="button"
              disabled={pending !== null}
              onClick={() => void revokeGrant(grant)}
            >
              Revoke {grant.label}
            </button>
          </li>
        ))}
      </ul>

      {workspace.host_approvals.length > 0 ? (
        <div className="workspace-standing-approvals">
          <h3>Permanent host command approvals</h3>
          <ul>
            {workspace.host_approvals.map((approval) => (
              <li key={approval.id}>
                <span>{approval.command_summary}</span>
                <span>{approval.cwd}</span>
                <span>Fingerprint {approval.fingerprint.slice(0, 16)}</span>
                <time dateTime={approval.created_at}>{approval.created_at}</time>
                <button
                  type="button"
                  disabled={pending !== null}
                  aria-label={`Revoke permanent approval ${approval.command_summary}`}
                  onClick={() => void revokeApproval(approval.id)}
                >
                  Revoke
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {workspace.tasks.some((task) => task.status === "running") ? (
        <div className="workspace-running-tasks">
          <h3>Running commands</h3>
          {workspace.tasks
            .filter((task) => task.status === "running")
            .map((task) => (
              <div key={task.task_id}>
                <span>{task.command_summary}</span>
                <button
                  type="button"
                  disabled={pending !== null}
                  onClick={() => void cancelTask(task.task_id)}
                >
                  Cancel command
                </button>
              </div>
            ))}
        </div>
      ) : null}

      {feedback ? <p role="status">{feedback}</p> : null}
      {failed ? (
        <p role="alert">Workspace settings couldn’t be changed. Try again.</p>
      ) : null}
    </section>
  );
}
