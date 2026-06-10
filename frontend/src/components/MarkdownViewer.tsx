import { useMemo, useState } from "react";
import { ImageOff } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

function headingId(children: unknown): string {
  return String(children)
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");
}

function MarkdownImage({ src, alt }: { src?: string; alt?: string }) {
  const [broken, setBroken] = useState(false);
  if (broken || !src) {
    return (
      <span role="img" aria-label={alt || "Image unavailable"} className="my-6 flex min-h-36 items-center justify-center rounded-2xl bg-slate-100 text-slate-500">
        <ImageOff className="mr-2 h-5 w-5" /> {alt || "Image unavailable"}
      </span>
    );
  }
  return (
    <img
      src={src}
      alt={alt ?? ""}
      onError={() => setBroken(true)}
      className="my-6 max-h-[34rem] w-full rounded-2xl border border-slate-200 object-contain"
    />
  );
}

export function MarkdownViewer({ markdown }: { markdown: string }) {
  const toc = useMemo(
    () =>
      markdown
        .split("\n")
        .filter((line) => line.startsWith("## "))
        .map((line) => {
          const label = line.slice(3).trim();
          return { label, id: headingId(label) };
        }),
    [markdown],
  );

  return (
    <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_220px]">
      <article className="markdown-body min-w-0 rounded-3xl border border-slate-200 bg-white p-6 shadow-sm sm:p-9">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            h2: ({ children }) => <h2 id={headingId(children)}>{children}</h2>,
            img: ({ src, alt }) => <MarkdownImage src={src} alt={alt} />,
            a: ({ children, ...props }) => <a {...props} target="_blank" rel="noreferrer">{children}</a>,
          }}
        >
          {markdown}
        </ReactMarkdown>
      </article>
      <aside className="order-first xl:order-last">
        <div className="sticky top-6 rounded-2xl border border-slate-200 bg-white p-5">
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-slate-400">On this page</p>
          <nav className="mt-4 space-y-3">
            {toc.length ? toc.map((item) => (
              <a key={item.id} href={`#${item.id}`} className="block text-sm text-slate-600 hover:text-blue-700">
                {item.label}
              </a>
            )) : <p className="text-sm text-slate-400">No sections found.</p>}
          </nav>
        </div>
      </aside>
    </div>
  );
}
