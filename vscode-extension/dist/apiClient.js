"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.ApiClient = void 0;
const vscode = __importStar(require("vscode"));
class ApiClient {
    constructor(log) {
        this.log = log;
    }
    get baseUrl() {
        const cfg = vscode.workspace.getConfiguration("localCodeAgent");
        return cfg.get("serverUrl", "http://127.0.0.1:8010").replace(/\/$/, "");
    }
    get apiKey() {
        const cfg = vscode.workspace.getConfiguration("localCodeAgent");
        return cfg.get("apiKey", "");
    }
    headers() {
        const headers = { "Content-Type": "application/json" };
        if (this.apiKey) {
            headers["Authorization"] = `Bearer ${this.apiKey}`;
        }
        return headers;
    }
    async post(path, body) {
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
            let json = {};
            try {
                json = text ? JSON.parse(text) : {};
            }
            catch (err) {
                const msg = err?.message || "Invalid JSON";
                this.log?.(`POST ${path} status ${res.status}: JSON parse error: ${msg}. Body (trunc): ${text.slice(0, 800)}`);
                return { ok: false, error: `Invalid JSON response: ${msg}` };
            }
            this.log?.(`POST ${path} status ${res.status}. Body (trunc): ${text.slice(0, 800)}`);
            if (!res.ok) {
                return { ok: false, error: json.detail || res.statusText };
            }
            return { ok: true, data: json };
        }
        catch (err) {
            const msg = err?.message || "Request failed";
            this.log?.(`POST ${path} failed: ${msg}`);
            return { ok: false, error: `${msg} (server: ${this.baseUrl})` };
        }
    }
    async postStream(path, body) {
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
        }
        catch (err) {
            const msg = err?.message || "Request failed";
            this.log?.(`POST ${path} failed: ${msg}`);
            return { ok: false, error: `${msg} (server: ${this.baseUrl})` };
        }
    }
    async get(path) {
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
            let json = {};
            try {
                json = text ? JSON.parse(text) : {};
            }
            catch (err) {
                const msg = err?.message || "Invalid JSON";
                this.log?.(`GET ${path} status ${res.status}: JSON parse error: ${msg}. Body (trunc): ${text.slice(0, 800)}`);
                return { ok: false, error: `Invalid JSON response: ${msg}` };
            }
            this.log?.(`GET ${path} status ${res.status}. Body (trunc): ${text.slice(0, 800)}`);
            if (!res.ok) {
                return { ok: false, error: json.detail || res.statusText };
            }
            return { ok: true, data: json };
        }
        catch (err) {
            const msg = err?.message || "Request failed";
            this.log?.(`GET ${path} failed: ${msg}`);
            return { ok: false, error: `${msg} (server: ${this.baseUrl})` };
        }
    }
}
exports.ApiClient = ApiClient;
