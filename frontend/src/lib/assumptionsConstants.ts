export const CATEGORY_ORDER = [
  'compute_sizing',
  'licensing',
  'storage',
  'networking',
  'commitment',
  'pricing_policy',
  'other',
]

export const CATEGORY_META: Record<string, { label: string; desc: string }> = {
  compute_sizing: { label: 'Compute Sizing', desc: 'vCPU:RAM ratios, machine-family defaults, instance-shape assumptions' },
  licensing: { label: 'Licensing', desc: 'OS licence fractions, BYOL handling, licence-tier bands' },
  storage: { label: 'Storage', desc: 'Disk-type mapping, IOPS/throughput assumptions' },
  networking: { label: 'Networking', desc: 'NAT VM-count assumptions, egress tiering' },
  commitment: { label: 'Commitment', desc: 'CUD/SP coverage and term assumptions' },
  pricing_policy: { label: 'Pricing Policy', desc: 'OD-vs-OD comparison basis, currency, catalog vintage' },
  other: { label: 'Other', desc: 'General and unclassified assumptions' },
}

