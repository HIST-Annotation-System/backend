import http from 'k6/http';
import { check, fail } from 'k6';
import { API } from './config.js';

// Logins are bcrypt checks on the server; an access token lasts 30 min, so a VU signs in again a little before that.
const TOKEN_LIFETIME_MS = 25 * 60 * 1000;

/**
 * One signed-in user. Every request carries a fixed `name` tag (ids replaced by placeholders, so one Prometheus series
 * per endpoint) and a `kind` tag the thresholds select on: api, tile, image, save, agreement, features, login, admin.
 */
export class Client {
  constructor(username, password) {
    this.username = username;
    this.password = password;
    this.token = null;
    this.loggedInAt = 0;
  }

  login() {
    const res = http.post(`${API}/auth/login`, JSON.stringify({ username: this.username, password: this.password }), {
      headers: { 'Content-Type': 'application/json' },
      tags: { name: 'POST /auth/login', kind: 'login' },
    });
    if (!check(res, { 'login: 200': (r) => r.status === 200 })) {
      fail(`login as ${this.username}: ${res.status} ${res.body}`);
    }
    const body = res.json();
    this.token = body.access_token;
    this.loggedInAt = Date.now();
    return body;
  }

  ensureLogin() {
    if (!this.token || Date.now() - this.loggedInAt > TOKEN_LIFETIME_MS) this.login();
  }

  params(name, kind = 'api', extra = {}) {
    return {
      headers: { Authorization: `Bearer ${this.token}`, 'Content-Type': 'application/json', ...extra.headers },
      tags: { name, kind },
      responseType: extra.responseType || 'text',
    };
  }

  request(method, path, body, name, kind, extra) {
    const payload = body === undefined ? null : JSON.stringify(body);
    return http.request(method, `${API}${path}`, payload, this.params(name, kind, extra));
  }

  get = (path, name, kind, extra) => this.request('GET', path, undefined, name, kind, extra);
  post = (path, body, name, kind, extra) => this.request('POST', path, body, name, kind, extra);
  put = (path, body, name, kind, extra) => this.request('PUT', path, body, name, kind, extra);
  patch = (path, body, name, kind, extra) => this.request('PATCH', path, body, name, kind, extra);
  del = (path, name, kind, extra) => this.request('DELETE', path, undefined, name, kind, extra);

  /** The response's JSON when its status is one of `ok`, else null (the failed check shows up in the summary). */
  json(res, label, ok = [200]) {
    const passed = check(res, { [`${label}: ${ok.join('/')}`]: (r) => ok.includes(r.status) });
    return passed && res.body ? res.json() : null;
  }

  /** Like `json`, but stops the script: for setup steps that the rest cannot do without. */
  must(res, label, ok = [200, 201, 202, 204]) {
    if (!ok.includes(res.status)) fail(`${label}: ${res.status} ${res.body}`);
    return res.body ? res.json() : null;
  }
}

/** Version-4 UUID for ids the client chooses (hotspot batches, annotations). */
export const uuid = () => crypto.randomUUID();

export const randomInt = (min, max) => Math.floor(min + Math.random() * (max - min + 1));
export const randomItem = (items) => items[Math.floor(Math.random() * items.length)];
