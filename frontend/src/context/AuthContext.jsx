import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import api from '../api/client';

const AuthContext = createContext(null);

/**
 * Cookie-based session. The JWT lives ONLY in the backend's httpOnly
 * `lf_access` cookie — never in localStorage — so an XSS payload cannot read
 * it. That means the frontend can't inspect a token to know if it's logged in;
 * instead it probes `/me`, which succeeds exactly when the cookie is valid.
 */
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [authed, setAuthed] = useState(false);
  const [loading, setLoading] = useState(true);

  const loadMe = useCallback(async () => {
    try {
      const res = await api.get('/me');
      setUser(res.data);
      setAuthed(true);
      return res.data;
    } catch {
      setUser(null);
      setAuthed(false);
      return null;
    }
  }, []);

  // Probe the session once on boot — a valid cookie restores the session.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      await loadMe();
      if (!cancelled) setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [loadMe]);

  const login = useCallback(
    async (email, password) => {
      // The response body still carries a token for API/CLI clients; the browser
      // ignores it and relies on the httpOnly cookie set by this same response.
      const res = await api.post('/login', { email, password });
      await loadMe();
      return res.data;
    },
    [loadMe]
  );

  const register = useCallback(
    async (email, password, name) => {
      const res = await api.post('/register', { email, password, name });
      await loadMe();
      return res.data;
    },
    [loadMe]
  );

  const logout = useCallback(async () => {
    try {
      await api.post('/logout'); // server clears the httpOnly cookie
    } catch {
      // even if the call fails, drop local state
    }
    setUser(null);
    setAuthed(false);
  }, []);

  return (
    <AuthContext.Provider value={{ authed, user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
