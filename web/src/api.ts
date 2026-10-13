import type { AuthProvider, DataProvider } from "@refinedev/core";

export async function api(path: string, options: RequestInit = {}) {
  const response = await fetch(`/api/${path}`, {
    ...options, credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Requested-With": "SimpleBooks", ...options.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw { message: typeof body.detail === "string" ? body.detail : "Request failed. Please check your input.", statusCode: response.status };
  }
  return response.status === 204 ? null : response.json();
}

export const authProvider: AuthProvider = {
  login: async ({ email, password }) => {
    try {
      const r = await fetch("/api/auth/login", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "SimpleBooks" },
        body: new URLSearchParams({ username: email, password }) });
      if (!r.ok) throw new Error("Invalid email or password, or too many login attempts.");
      return { success: true, redirectTo: "/" };
    } catch (e) { return { success: false, error: { name: "Sign-in failed", message: (e as Error).message } }; }
  },
  logout: async () => { await api("auth/logout", { method: "POST" }); return { success: true, redirectTo: "/login" }; },
  check: async () => {
    try { await api("users/me"); return { authenticated: true }; }
    catch { return { authenticated: false, redirectTo: "/login" }; }
  },
  getIdentity: async () => { const u = await api("users/me"); return { ...u, name: u.email }; },
  getPermissions: async () => (await api("users/me")).role,
  onError: async (error) => error.statusCode === 401 ? { logout: true, redirectTo: "/login" } : { error },
};

export const dataProvider: DataProvider = {
  getList: async ({ resource, filters, pagination }) => {
    let rows = await api(resource);
    for (const f of filters || []) {
      if ("field" in f && f.value) rows = rows.filter((r: any) => String(r[f.field]).toLowerCase().includes(String(f.value).toLowerCase()));
    }
    const total = rows.length;
    const current = pagination?.currentPage || 1, size = pagination?.pageSize || 20;
    if (pagination?.mode !== "off") rows = rows.slice((current - 1) * size, current * size);
    return { data: rows, total };
  },
  getOne: async ({ resource, id }) => {
    const rows = await api(resource); return { data: rows.find((r: any) => r.id === Number(id)) };
  },
  create: async ({ resource, variables }) => ({ data: await api(resource, { method: "POST", body: JSON.stringify(variables) }) }),
  update: async ({ resource, id, variables }) => ({ data: await api(`${resource}/${id}`, { method: "PATCH", body: JSON.stringify(variables) }) }),
  deleteOne: async () => { throw new Error("Posted accounting records cannot be deleted directly."); },
  getApiUrl: () => "/api",
};
