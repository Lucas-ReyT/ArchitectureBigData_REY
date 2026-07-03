import { useState } from "react";
import { Link } from "react-router-dom";
import { useSearchEnterprisesQuery } from "../api/apiSlice";

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [scrapedOnly, setScrapedOnly] = useState(false);
  const { data: results, isFetching } = useSearchEnterprisesQuery(
    { q: query, scrapedOnly },
    { skip: query.trim().length < 2 }
  );

  return (
    <>
      <h1>Recherche entreprise</h1>
      <input
        className="search-input"
        type="text"
        placeholder="Nom ou numero BCE (ex. Maison Internationale, 0401.144.191)"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        autoFocus
      />

      <label className="scraped-toggle">
        <input
          type="checkbox"
          checked={scrapedOnly}
          onChange={(e) => setScrapedOnly(e.target.checked)}
        />
        Uniquement les entreprises deja scrapees (demo)
      </label>

      {isFetching && <p className="empty-state">Recherche…</p>}

      {!isFetching && query.trim().length >= 2 && results?.length === 0 && (
        <p className="empty-state">Aucun resultat</p>
      )}

      <ul className="result-list">
        {results?.map((r) => (
          <li className="result-item" key={r.enterprise_number}>
            <Link to={`/enterprise/${r.enterprise_number}`}>
              <div className="result-name">{r.name || "(nom inconnu)"}</div>
              <div className="result-meta">
                {r.enterprise_number}
                {r.legal_form_label ? ` · ${r.legal_form_label}` : ""}
                {r.address?.city_fr ? ` · ${r.address.city_fr}` : ""}
                {r.status_label ? ` · ${r.status_label}` : ""}
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}
