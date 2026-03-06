interface PageHeaderProps {
  title: string;
  subtitle: string;
}

export function PageHeader({ title, subtitle }: PageHeaderProps) {
  return (
    <section className="space-y-2">
      <h1 className="text-4xl md:text-5xl font-serif text-primary">{title}</h1>
      <p className="text-lg text-primary/60 font-sans">{subtitle}</p>
    </section>
  );
}
