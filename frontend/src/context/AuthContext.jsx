import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import api from '../api/client';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem('trax9_token'));
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(() => !!localStorage.getItem('trax9_token'));

  // Whenever we hold a token, resolve the operator identity
  useEffect(() => {
    if (!token) {
      setUser(null);
      setLoading(false);
      return undefined;
    }
    let cancelled = false;
    setLoading(true);
    api
      .get('/me')
      .then((res) => {
        if (!cancelled) setUser(res.data);
      })
      .catch(() => {
        // 401 is handled globally by the client interceptor
        if (!cancelled) setUser(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const login = useCallback(async (email, password) => {
    const res = await api.post('/login', { email, password });
    localStorage.setItem('trax9_token', res.data.access_token);
    setToken(res.data.access_token);
    return res.data;
  }, []);

  const register = useCallback(async (email, password, name) => {
    const res = await api.post('/register', { email, password, name });
    localStorage.setItem('trax9_token', res.data.access_token);
    setToken(res.data.access_token);
    return res.data;
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem('trax9_token');
    setToken(null);
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ token, user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
