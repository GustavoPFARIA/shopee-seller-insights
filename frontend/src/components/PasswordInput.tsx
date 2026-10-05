import { useState, type InputHTMLAttributes } from 'react'

/** Password field with a show/hide toggle (avoids typos when choosing a password). */
export default function PasswordInput(props: Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>) {
  const [visible, setVisible] = useState(false)
  return (
    <span className="password-field">
      <input {...props} type={visible ? 'text' : 'password'} />
      <button
        type="button"
        className="link-button small"
        aria-label={visible ? 'Hide password' : 'Show password'}
        onClick={() => setVisible((v) => !v)}
      >
        {visible ? 'Hide' : 'Show'}
      </button>
    </span>
  )
}
