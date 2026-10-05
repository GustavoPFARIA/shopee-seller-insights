import type { ApiError } from '../api'

/** Shows an API error and, for file imports, the row-level validation problems. */
export default function ErrorDetails({ error }: { error: ApiError }) {
  return (
    <div className="error">
      <p>{error.message}</p>
      {error.details.length > 0 && (
        <ul>
          {error.details.map((d, i) => (
            <li key={i}>
              {d.row ? `Row ${d.row}, ` : ''}column “{d.field}”: {d.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
