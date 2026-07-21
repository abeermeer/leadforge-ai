import axios from 'axios';

const client = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
});

// Attach bearer token from localStorage on every request
client.interceptors.request.use((config) => {
  const token = localStorage.getItem('trax9_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// On 401 (expired/invalid token) clear the token and bounce to /login.
// Auth endpoints themselves are exempt so a bad password doesn't loop.
client.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response ? error.response.status : null;
    const url = (error.config && error.config.url) || '';
    const isAuthCall = url.includes('/login') || url.includes('/register');
    if (status === 401 && !isAuthCall) {
      localStorage.removeItem('trax9_token');
      if (window.location.pathname !== '/login') {
        window.location.assign('/login');
      }
    }
    return Promise.reject(error);
  }
);

export default client;
