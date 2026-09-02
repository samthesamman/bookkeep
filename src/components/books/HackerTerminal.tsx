import { useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';

/**
 * Purely cosmetic "movie hacker" terminal. Shown while a request is being
 * submitted so the wait feels like something dramatic is happening. None of
 * the text below is real — it's random flavor.
 */

const HOSTS = [
  '10.0.0.1', '192.168.1.34', 'node-7', 'gw.internal', 'db-primary',
  'edge-04', 'vault', 'archive-3', '172.16.9.2', 'proxy-11',
];

const ACTIONS = [
  'establishing tunnel to {host}',
  'bypassing firewall on {host} ...',
  'injecting payload → {host}',
  'brute-forcing SSH keys @ {host}',
  'decrypting AES-256 block {n}/{m}',
  'spoofing MAC address {mac}',
  'rerouting through {host}',
  'dumping memory region 0x{hex}',
  'cracking hash {hex}{hex}',
  'escalating privileges on {host}',
  'disabling intrusion detection',
  'exfiltrating catalog shard {n}',
  'compiling exploit for CVE-{cve}',
  'scanning ports 1-65535 on {host}',
  'ACCESS GRANTED :: {host}',
  'planting backdoor on {host}',
  'wiping logs from {host}',
  'downloading manifest [{bar}]',
  'handshake with {host} :: OK',
  'defeating 2FA challenge ...',
  'tracing packet route to {host}',
];

function hex(len: number) {
  let s = '';
  for (let i = 0; i < len; i++) s += '0123456789abcdef'[Math.floor(Math.random() * 16)];
  return s;
}

function pick<T>(arr: T[]) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function makeLine() {
  const bars = Math.floor(Math.random() * 20);
  return pick(ACTIONS)
    .replace('{host}', pick(HOSTS))
    .replace(/\{n\}/g, String(Math.floor(Math.random() * 900) + 100))
    .replace(/\{m\}/g, String(Math.floor(Math.random() * 400) + 600))
    .replace('{mac}', Array.from({ length: 6 }, () => hex(2)).join(':'))
    .replace(/\{hex\}/g, () => hex(8))
    .replace('{cve}', `2024-${Math.floor(Math.random() * 9000) + 1000}`)
    .replace('{bar}', '#'.repeat(bars) + '-'.repeat(20 - bars));
}

interface HackerTerminalProps {
  className?: string;
  label?: string;
}

export function HackerTerminal({ className, label = 'Submitting request' }: HackerTerminalProps) {
  const [lines, setLines] = useState<string[]>(() => [
    '$ ./request --target catalog --mode acquire',
    'initializing ...',
  ]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const id = window.setInterval(() => {
      setLines((prev) => {
        const next = [...prev, makeLine()];
        return next.length > 40 ? next.slice(next.length - 40) : next;
      });
    }, 140 + Math.random() * 160);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [lines]);

  return (
    <div
      className={cn(
        'rounded-lg border border-green-500/30 bg-black p-3 font-mono text-xs text-green-400 shadow-inner',
        className
      )}
    >
      <div className="mb-2 flex items-center gap-1.5 border-b border-green-500/20 pb-2">
        <span className="h-2.5 w-2.5 rounded-full bg-red-500/70" />
        <span className="h-2.5 w-2.5 rounded-full bg-yellow-500/70" />
        <span className="h-2.5 w-2.5 rounded-full bg-green-500/70" />
        <span className="ml-2 text-green-500/60">{label}</span>
      </div>
      <div ref={scrollRef} className="h-40 overflow-hidden leading-relaxed">
        {lines.map((line, i) => (
          <div key={i} className="whitespace-pre-wrap break-all">
            <span className="text-green-600">&gt;</span> {line}
          </div>
        ))}
        <div className="inline-block h-3.5 w-2 animate-pulse bg-green-400 align-middle" />
      </div>
    </div>
  );
}
