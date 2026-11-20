// API client. Errors arrive as Error with the server's message.

async function request(method: string, path: string, body?: unknown) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* the response was not JSON */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  request,
  get: (path: string) => request("GET", path),
  post: (path: string, body: unknown = {}) => request("POST", path, body),
  put: (path: string, body: unknown) => request("PUT", path, body),
};

/** Waits for a background job to finish, reporting progress. */
export async function waitJob(job: any, onProgress: (text: string) => void) {
  let current = job;
  while (current.status === "running") {
    onProgress(current.progress ? `${current.label}: ${current.progress}` : `${current.label}…`);
    await new Promise((r) => setTimeout(r, 500));
    current = await api.get(`/jobs/${current.id}`);
  }
  if (current.status === "error") throw new Error(current.error);
  return current.result;
}

export const fmt = (x: number | null | undefined) => (x == null ? "—" : x.toFixed(3));
