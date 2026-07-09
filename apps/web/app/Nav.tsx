'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const navItems = [
  { href: '/', label: 'Dashboard' },
  { href: '/incidents', label: 'Incidents' },
  { href: '/agents', label: 'Agents' },
  { href: '/runs', label: 'Runs' },
  { href: '/agent/runs', label: 'Agent Runs' },
  { href: '/approvals', label: 'Approvals' },
  { href: '/accounts', label: 'Accounts' },
  { href: '/support/tickets', label: 'Support' },
  { href: '/knowledge', label: 'Knowledge' },
  { href: '/evals', label: 'Evals' },
];

function isActive(pathname: string | null, href: string): boolean {
  if (pathname === null) return false;
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function Nav() {
  const pathname = usePathname();

  return (
    <nav className="global-nav" aria-label="Primary">
      <div className="global-nav-inner">
        <Link href="/" className="global-nav-brand">
          Ops Agent
        </Link>
        <ul className="global-nav-list">
          {navItems.map((item) => {
            const active = isActive(pathname, item.href);
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  aria-current={active ? 'page' : undefined}
                  className={active ? 'nav-link-active' : undefined}
                >
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </div>
    </nav>
  );
}
