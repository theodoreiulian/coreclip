import React from 'react';
import { CheckIcon, XIcon } from './Icons';

const STAGES = [
  "ingesting",
  "analyzing",
  "clipping",
  "processing",
  "captioning",
  "ready_for_review",
  "posting",
  "completed"
];

export default function PipelineStatus({ currentStage, status, errorMessage }: { currentStage: string, status: string, errorMessage?: string }) {
  const currentIndex = STAGES.indexOf(currentStage);

  return (
    <div className="flex items-start gap-0 overflow-x-auto pb-2 pt-5">
      {STAGES.map((stage, idx) => {
        const isCompleted = idx < currentIndex || status === 'completed';
        const isCurrent = idx === currentIndex && status !== 'completed' && status !== 'failed';
        const isFailed = idx === currentIndex && status === 'failed';
        const isLast = idx === STAGES.length - 1;

        return (
          <div key={stage} className="flex flex-none items-start">
            <div className="flex flex-col items-center gap-2" title={isFailed ? errorMessage : ''}>
              <div
                className={`grid h-9 w-9 place-items-center rounded-full text-xs font-semibold transition
                  ${isCompleted ? 'bg-gradient-to-br from-emerald-400 to-emerald-600 text-white shadow-[0_0_16px_-2px_rgba(16,185,129,0.7)]' : ''}
                  ${isCurrent ? 'bg-gradient-to-br from-blue-500 to-cyan-500 text-white glow-ring' : ''}
                  ${isCurrent ? 'animate-pulse' : ''}
                  ${isFailed ? 'bg-gradient-to-br from-rose-500 to-red-600 text-white shadow-[0_0_16px_-2px_rgba(244,63,94,0.7)]' : ''}
                  ${!isCompleted && !isCurrent && !isFailed ? 'border border-white/15 bg-white/[0.03] text-[var(--text-faint)]' : ''}
                `}
              >
                {isCompleted ? <CheckIcon width={16} height={16} /> : isFailed ? <XIcon width={15} height={15} /> : idx + 1}
              </div>
              <span
                className={`max-w-[5.5rem] text-center text-[11px] capitalize leading-tight transition
                  ${isCurrent ? 'font-semibold text-cyan-300' : ''}
                  ${isFailed ? 'font-semibold text-rose-300' : ''}
                  ${isCompleted ? 'text-emerald-300/90' : ''}
                  ${!isCompleted && !isCurrent && !isFailed ? 'text-[var(--text-faint)]' : ''}
                `}
              >
                {stage.replace(/_/g, ' ')}
              </span>
            </div>
            {!isLast && (
              <div className="relative mx-1 mt-4 h-[3px] w-10 overflow-hidden rounded-full bg-white/[0.07]">
                <div
                  className={`absolute inset-0 rounded-full transition-all duration-500 ${
                    isCompleted ? 'bg-gradient-to-r from-emerald-400 to-emerald-500' : 'w-0'
                  }`}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
