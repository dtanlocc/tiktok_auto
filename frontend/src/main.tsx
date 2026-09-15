import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { SecureBootstrap } from './components/SecureBootstrap.tsx'
import { installSecureTransport } from './services/secureTransport.ts'

installSecureTransport()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <SecureBootstrap>
      <App />
    </SecureBootstrap>
  </StrictMode>,
)
