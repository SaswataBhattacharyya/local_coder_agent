import * as vscode from "vscode";

export type ApiResponse<T> = { ok: true; data: T } | { ok: false; error: string };

export class ApiClient {
  constructor(private log?: (msg: string) => void) {}
  private get baseUrl(): string {
    const cfg = vscode.workspace.getConfiguration("localCodeAgent");
    return cfg.get<string>("serverUrl", "http://127.0.0.1:8010").replace(/\/$/, "");
  }

  private get apiKey(): string {
    const cfg = vscode.workspace.getConfiguration("localCodeAgent");
    return cfg.get<string>("apiKey", "");
  }

  private headers(): Record<string, string> {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (this.apiKey) {
      headers["Authorization"] = `Bearer ${this.apiKey}`;
    }
    return headers;
  }

  async post<T>(path: string, body: any): Promise<ApiResponse<T>> {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 60000);
      const res = await fetch(`${this.baseUrl}${path}`, {
        method: "POST",
        headers: this.headers(),
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      clearTimeout(timeout);
      const text = await res.text();
      let json: any = {};
      try {
        json = text ? JSON.parse(text) : {};
      } catch (err: any) {
        const msg = err?.message || "Invalid JSON";
        this.log?.(`POST ${path} status ${res.status}: JSON parse error: ${msg}. Body (trunc): ${text.slice(0, 800)}`);
        return { ok: false, error: `Invalid JSON response: ${msg}` };
      }
      this.log?.(`POST ${path} status ${res.status}. Body (trunc): ${text.slice(0, 800)}`);
      if (!res.ok) {
        return { ok: false, error: json.detail || res.statusText };
      }
      return { ok: true, data: json as T };
    } catch (err: any) {
      const msg = err?.message || "Request failed";
      this.log?.(`POST ${path} failed: ${msg}`);
      return { ok: false, error: `${msg} (server: ${this.baseUrl})` };
    }
  }

  async postStream(
    path: string,
    body: any
  ): Promise<{ ok: true; reader: ReadableStreamDefaultReader<Uint8Array> } | { ok: false; error: string }> {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 120000);
      const res = await fetch(`${this.baseUrl}${path}`, {
        method: "POST",
        headers: this.headers(),
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      clearTimeout(timeout);
      if (!res.ok || !res.body) {
        const text = await res.text();
        this.log?.(`POST ${path} status ${res.status}. Body (trunc): ${text.slice(0, 800)}`);
        return { ok: false, error: text || res.statusText };
      }
      this.log?.(`POST ${path} status ${res.status} (streaming).`);
      return { ok: true, reader: res.body.getReader() };
    } catch (err: any) {
      const msg = err?.message || "Request failed";
      this.log?.(`POST ${path} failed: ${msg}`);
      return { ok: false, error: `${msg} (server: ${this.baseUrl})` };
    }
  }

  async get<T>(path: string): Promise<ApiResponse<T>> {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 60000);
      const res = await fetch(`${this.baseUrl}${path}`, {
        method: "GET",
        headers: this.headers(),
        signal: controller.signal,
      });
      clearTimeout(timeout);
      const text = await res.text();
      let json: any = {};
      try {
        json = text ? JSON.parse(text) : {};
      } catch (err: any) {
        const msg = err?.message || "Invalid JSON";
        this.log?.(`GET ${path} status ${res.status}: JSON parse error: ${msg}. Body (trunc): ${text.slice(0, 800)}`);
        return { ok: false, error: `Invalid JSON response: ${msg}` };
      }
      this.log?.(`GET ${path} status ${res.status}. Body (trunc): ${text.slice(0, 800)}`);
      if (!res.ok) {
        return { ok: false, error: json.detail || res.statusText };
      }
      return { ok: true, data: json as T };
    } catch (err: any) {
      const msg = err?.message || "Request failed";
      this.log?.(`GET ${path} failed: ${msg}`);
      return { ok: false, error: `${msg} (server: ${this.baseUrl})` };
    }
  }
}
