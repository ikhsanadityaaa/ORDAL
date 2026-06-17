import { create } from 'zustand'
import api from '../api'

function loadStoredUser() {
  try {
    const raw = localStorage.getItem('user')
    if (!raw) return null
    return JSON.parse(raw)
  } catch (err) {
    console.error('Error loading user from localStorage:', err)
    try {
      localStorage.removeItem('user')
      localStorage.removeItem('token')
    } catch (e) {
      console.error('Error clearing localStorage:', e)
    }
    return null
  }
}

function loadStoredToken() {
  try {
    return localStorage.getItem('token') || null
  } catch (err) {
    console.error('Error loading token from localStorage:', err)
    return null
  }
}

const useAuthStore = create((set) => ({
  user:  loadStoredUser(),
  token: loadStoredToken(),

  setAuth: (token, user) => {
    localStorage.setItem('token', token)
    localStorage.setItem('user', JSON.stringify(user))
    set({ token, user })
  },

  login: async (email, password) => {
    const res = await api.post('/auth/login', { email, password })
    localStorage.setItem('token', res.data.token)
    localStorage.setItem('user', JSON.stringify(res.data.user))
    set({ token: res.data.token, user: res.data.user })
  },

  register: async (name, email, password) => {
    const res = await api.post('/auth/register', { name, email, password })
    localStorage.setItem('token', res.data.token)
    localStorage.setItem('user', JSON.stringify(res.data.user))
    set({ token: res.data.token, user: res.data.user })
  },

  logout: () => {
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    set({ token: null, user: null })
  },
}))

export default useAuthStore
