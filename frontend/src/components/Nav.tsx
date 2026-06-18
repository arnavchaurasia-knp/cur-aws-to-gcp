import facetsFIcon from '../assets/facets-logo-f.svg'
import type { UserInfo } from '../api/auth'
import { logoutURL } from '../api/auth'
import { Link } from 'react-router-dom'

export function Nav({ user }: { user: UserInfo }) {
  return (
    <nav className="flex items-center justify-between px-6 py-3 border-b border-white/10">
      <Link to="/" className="flex items-center gap-3 hover:opacity-90 transition">
        <img src={facetsFIcon} alt="Facets" className="h-5" />
        <span className="hidden sm:inline text-white/30">|</span>
        <span className="text-sm font-semibold tracking-tight">
          <span className="hidden sm:inline">AWS → GCP Cost Estimator</span>
          <span className="sm:hidden">Estimator</span>
        </span>
      </Link>
      <div className="flex items-center gap-4 text-sm text-gray-400">
        {user.is_admin && (
          <Link to="/admin" className="text-[#00C2BB] hover:underline">All reports</Link>
        )}
        <span className="hidden sm:inline">{user.email}</span>
        <a href={logoutURL()} className="text-[#645DF6] hover:underline">Sign out</a>
      </div>
    </nav>
  )
}
