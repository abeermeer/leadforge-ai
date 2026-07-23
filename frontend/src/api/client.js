import axios from 'axios';

/**
 * Auth is the httpOnly `lf_access` cookie set by the backend on login/register.
 * The token is deliberately NEVER stored in localStorage or any other
 * JS-readable place — that is the whole point of the httpOnly cookie: an XSS
 * payload cannot read or exfiltrate the session. `withCredentials` makes the
 * browser attach the cookie to every /api call.
 */
const client = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
});

// On 401 (expired/invalid session) bounce to /login. Auth calls and the /me
// probe are exempt — a bad password or a logged-out visitor is handled by
// AuthContext state, not a hard redirect loop.
client.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response ? error.response.status : null;
    const url = (error.config && error.config.url) || '';
    const isAuthCall =
      url.includes('/login') || url.includes('/register') || url.includes('/me');
    if (status === 401 && !isAuthCall) {
      if (window.location.pathname !== '/login') {
        window.location.assign('/login');
      }
    }
    return Promise.reject(error);
  }
);

export default client;
