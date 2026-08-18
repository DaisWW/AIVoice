export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export class ApiClient {
  async request(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    if (!response.ok) {
      throw new ApiError(await this.#errorMessage(response), response.status);
    }
    if (response.status === 204) return null;
    return response.json();
  }

  get(path, options = {}) {
    return this.request(path, options);
  }

  postForm(path, body, options = {}) {
    return this.request(path, { ...options, method: "POST", body });
  }

  post(path, body, options = {}) {
    return this.request(path, {
      ...options,
      method: "POST",
      headers: { "Content-Type": "application/json", ...options.headers },
      body: JSON.stringify(body),
    });
  }

  patch(path, body, options = {}) {
    return this.request(path, {
      ...options,
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...options.headers },
      body: JSON.stringify(body),
    });
  }

  put(path, body, options = {}) {
    return this.request(path, {
      ...options,
      method: "PUT",
      headers: { "Content-Type": "application/json", ...options.headers },
      body: JSON.stringify(body),
    });
  }

  delete(path, options = {}) {
    return this.request(path, { ...options, method: "DELETE" });
  }

  async #errorMessage(response) {
    const fallback = `请求失败 (${response.status})`;
    try {
      const body = await response.json();
      return body.detail || fallback;
    } catch (_) {
      return fallback;
    }
  }
}
