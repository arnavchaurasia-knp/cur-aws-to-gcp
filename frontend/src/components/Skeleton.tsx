type SkeletonProps = {
  /** Tailwind width/height/shape classes, e.g. "h-4 w-32 rounded-full" */
  className?: string
}

/**
 * Reusable loading placeholder. Use instead of blank screens or spinners while
 * content is loading. Match the dimensions of the real content it replaces.
 *
 * Example:
 *   <Skeleton className="h-4 w-40" />
 *   <Skeleton className="h-24 w-full rounded-xl" />
 */
export function Skeleton({ className = '' }: SkeletonProps) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />
}

/** A block of stacked text-line skeletons. */
export function SkeletonText({ lines = 3, className = '' }: { lines?: number; className?: string }) {
  return (
    <div className={`flex flex-col gap-2 ${className}`} aria-hidden="true">
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="skeleton h-3"
          style={{ width: i === lines - 1 ? '60%' : '100%' }}
        />
      ))}
    </div>
  )
}

