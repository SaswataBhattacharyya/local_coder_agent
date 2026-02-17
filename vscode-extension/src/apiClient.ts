import * as vscode from "vscode";

export type ApiResponse<T> = { ok: true; data: T } | { ok: false; error: string };

export class ApiClient {
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
      const res = await fetch(`${this.baseUrl}${path}`, {
        method: "POST",
        headers: this.headers(),
        body: JSON.stringify(body),
      });
      const text = await res.text();
      const json = text ? JSON.parse(text) : {};
      if (!res.ok) {
        return { ok: false, error: json.detail || res.statusText };
      }
      return { ok: true, data: json as T };
    } catch (err: any) {
      return { ok: false, error: err?.message || "Request failed" };
    }
  }

  async get<T>(path: string): Promise<ApiResponse<T>> {
    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        method: "GET",
        headers: this.headers(),
      });
      const text = await res.text();
      const json = text ? JSON.parse(text) : {};
      if (!res.ok) {
        return { ok: false, error: json.detail || res.statusText };
      }
      return { ok: true, data: json as T };
    } catch (err: any) {
      return { ok: false, error: err?.message || "Request failed" };
    }
  }
}
