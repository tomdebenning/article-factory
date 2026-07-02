import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type CompletedArticle } from "../api";
import { formatItemTime } from "../utils/sixHourSlots";
import { formatTokenCount } from "../utils/tokenFormat";

function ArticleRow({ article }: { article: CompletedArticle }) {
  const hasContent = article.has_content ?? Boolean(article.body_markdown?.trim());
  return (
    <li className="article-list-item">
      <Link to={`/articles/${article.run_id}`}>
        <strong>{article.title || "Untitled artifact"}</strong>
      </Link>
      {!hasContent && <span className="queue-status-badge status-failed">No content</span>}
      <p className="hint">
        Model used: {article.model || "—"} · In {formatTokenCount(article.stats?.input_tokens)} · Out{" "}
        {formatTokenCount(article.stats?.output_tokens)} · Total {formatTokenCount(article.stats?.total_tokens)}
      </p>
      {hasContent ? (
        <p className="hint">{article.summary}</p>
      ) : (
        <p className="hint">This run finished without saved article text. Open it to view step or workspace files.</p>
      )}
      <p className="hint">{formatItemTime(article.created_at)}</p>
    </li>
  );
}

export default function ArticlesPage() {
  const [articles, setArticles] = useState<CompletedArticle[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void api
      .listArticles()
      .then((data) => setArticles(data.articles))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const sortedArticles = useMemo(
    () =>
      [...articles].sort((a, b) => {
        const contentA = a.has_content ?? Boolean(a.body_markdown?.trim());
        const contentB = b.has_content ?? Boolean(b.body_markdown?.trim());
        if (contentA !== contentB) {
          return contentA ? -1 : 1;
        }
        return (b.created_at ?? "").localeCompare(a.created_at ?? "");
      }),
    [articles],
  );

  if (error) {
    return <p className="error">{error}</p>;
  }

  if (loading) {
    return (
      <section className="card articles-page">
        <p className="hint">Loading artifacts…</p>
      </section>
    );
  }

  return (
    <section className="card articles-page">
      <h2>Completed artifacts</h2>
      <p className="hint">
        Finished articles from the factory. Click a title to read the full artifact, step outputs, and workspace files.
      </p>
      {sortedArticles.length === 0 ? (
        <p className="hint">No completed artifacts yet.</p>
      ) : (
        <ul className="article-list">
          {sortedArticles.map((article) => (
            <ArticleRow key={article.run_id} article={article} />
          ))}
        </ul>
      )}
    </section>
  );
}
