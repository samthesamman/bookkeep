import type { ImgHTMLAttributes } from 'react';

type BookkeepLogoProps = ImgHTMLAttributes<HTMLImageElement> & {
  title?: string;
};

export function BookkeepLogo({ title = 'Bookstore', className, ...props }: BookkeepLogoProps) {
  return (
    <img
      src="/favicon.svg"
      alt={title}
      className={['block h-full w-full object-contain', className].filter(Boolean).join(' ')}
      {...props}
    />
  );
}
