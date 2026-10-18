import { useEffect } from 'react'

const SUFFIX = ' · Facets | AWS → GCP Cost Estimator'

export function useTitle(prefix: string) {
  useEffect(() => {
    const prev = document.title
    document.title = prefix + SUFFIX
    return () => { document.title = prev }
  }, [prefix])
}
