import React, { useEffect, useRef, useState } from 'react';
import { ChevronDownIcon, CheckIcon } from './Icons';

export interface SelectOption {
  value: string;
  label: string;
}

// A fully themed dropdown that replaces the native <select> (whose option list
// is drawn by the OS and can't be styled). Same contract as a controlled
// select: `value` + `onChange(value)`.
export default function Select({
  value,
  onChange,
  options,
  className = '',
}: {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const current = options.find(o => o.value === value);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className={`relative ${className}`}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="select-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="truncate">{current?.label ?? 'Select…'}</span>
        <ChevronDownIcon width={16} height={16} className="chev" />
      </button>

      {open && (
        <div className="select-menu fade-in" role="listbox">
          {options.map(o => (
            <button
              key={o.value}
              type="button"
              role="option"
              aria-selected={o.value === value}
              data-selected={o.value === value}
              onClick={() => { onChange(o.value); setOpen(false); }}
              className="select-option"
            >
              <span className="truncate">{o.label}</span>
              {o.value === value && <CheckIcon width={15} height={15} className="opt-check" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
