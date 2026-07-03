import { useState, useMemo } from "react";
import { useParams, Link } from "react-router-dom";
import { useGetEnterpriseQuery, useGetDocumentsQuery, useGetRepresentativesQuery, API_BASE } from "../api/apiSlice";
import SankeyDiagram from "../components/SankeyDiagram";
import TrendChart from "../components/TrendChart";

function fmt(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return value.toLocaleString("fr-BE", { maximumFractionDigits: 2 });
  return value;
}

export default function EnterprisePage() {
  const { num } = useParams();
  const { data, isFetching, isError } = useGetEnterpriseQuery(num);
  const { data: documents } = useGetDocumentsQuery(num);
  const { data: repsData, isFetching: repsLoading } = useGetRepresentativesQuery(num);
  const [selectedYear, setSelectedYear] = useState(null);

  const years = data?.gold?.years ?? [];
  const currentYear = useMemo(() => {
    if (!years.length) return null;
    return years.find((y) => y.year === selectedYear) ?? years[years.length - 1];
  }, [years, selectedYear]);

  if (isFetching) return <p className="empty-state">Chargement…</p>;
  if (isError || !data) return <p className="empty-state">Entreprise introuvable.</p>;

  const { silver, gold } = data;

  return (
    <>
      <Link className="back-link" to="/">← Retour a la recherche</Link>

      <div className="card">
        <p className="entreprise-name">{silver.name || "(nom inconnu)"}</p>
        <p className="entreprise-sub">{silver.enterprise_number}</p>
        <div className="field-grid">
          <div>
            <div className="field-label">Forme juridique</div>
            <div className="field-value">{silver.legal_form_label || silver.legal_form}</div>
          </div>
          <div>
            <div className="field-label">Statut</div>
            <div className="field-value">{silver.status_label || silver.status}</div>
          </div>
          <div>
            <div className="field-label">Adresse</div>
            <div className="field-value">
              {silver.address?.street_fr} {silver.address?.house_number}
              <br />
              {silver.address?.zip} {silver.address?.city_fr}
            </div>
          </div>
          <div>
            <div className="field-label">Date de debut</div>
            <div className="field-value">{silver.start_date || "—"}</div>
          </div>
        </div>
      </div>

      <div className="card">
        <h2>Activites NACE</h2>
        {(silver.activities ?? []).map((a, i) => (
          <div className="activity-row" key={i}>
            <span>{a.nace_label || a.nace_code}</span>
            <span>
              {a.nace_code}
              <span className="tag">{a.classification}</span>
            </span>
          </div>
        ))}
      </div>

      <div className="card">
        <h2>Dirigeants</h2>
        {repsLoading && <p className="empty-state">Recherche sur kbopub…</p>}
        {!repsLoading && (repsData?.representatives ?? []).length === 0 && (
          <p className="empty-state">Aucun dirigeant trouve.</p>
        )}
        {(repsData?.representatives ?? []).map((r, i) => (
          <div className="activity-row" key={i}>
            <span>
              {r.name || (r.represented_enterprise_number
                ? `${r.represented_enterprise_number} (personne morale)`
                : "(inconnu)")}
            </span>
            <span>
              {r.since ? `Depuis ${r.since}` : ""}
              <span className="tag">{r.role}</span>
            </span>
          </div>
        ))}
      </div>

      <div className="card">
        <h2>Ratios financiers</h2>
        {!gold ? (
          <p className="empty-state">Pas encore de donnees financieres scrapees pour cette entreprise.</p>
        ) : (
          <>
            <select
              className="year-select"
              value={currentYear?.year}
              onChange={(e) => setSelectedYear(Number(e.target.value))}
            >
              {years.map((y) => (
                <option key={y.year} value={y.year}>{y.year}</option>
              ))}
            </select>

            <SankeyDiagram
              ca={currentYear.ca}
              margeBrute={currentYear.ratios.marge_brute}
              resultatNet={currentYear.resultat_net}
            />

            {years.length > 1 && (
              <div className="trend-grid">
                <TrendChart
                  title="Chiffre d'affaires par annee"
                  years={years.map((y) => y.year)}
                  values={years.map((y) => y.ca)}
                  colorFor={() => "var(--series-1)"}
                />
                <TrendChart
                  title="Resultat net par annee"
                  years={years.map((y) => y.year)}
                  values={years.map((y) => y.resultat_net)}
                  colorFor={(v) => (v >= 0 ? "var(--status-good)" : "var(--status-critical)")}
                />
              </div>
            )}

            <table className="ratios">
              <thead>
                <tr>
                  <th>Annee</th>
                  <th>CA</th>
                  <th>Marge nette (%)</th>
                  <th>ROE (%)</th>
                  <th>Ratio de liquidite</th>
                  <th>Taux d'endettement (%)</th>
                </tr>
              </thead>
              <tbody>
                {years.map((y) => (
                  <tr key={y.year}>
                    <td>{y.year}</td>
                    <td>{fmt(y.ca)}</td>
                    <td>{fmt(y.ratios.marge_nette)}</td>
                    <td>{fmt(y.ratios.roe)}</td>
                    <td>{fmt(y.ratios.ratio_liquidite)}</td>
                    <td>{fmt(y.ratios.taux_endettement)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>

      {documents && documents.length > 0 && (
        <div className="card">
          <h2>Documents</h2>
          {Object.entries(
            documents.reduce((byYear, d) => {
              (byYear[d.year] ??= []).push(d);
              return byYear;
            }, {})
          )
            .sort(([a], [b]) => b - a)
            .map(([year, docs]) => (
              <div className="activity-row" key={year}>
                <span>{year}</span>
                <span>
                  {docs.map((d) => (
                    <a
                      key={d.file_type}
                      className="doc-link"
                      href={`${API_BASE}/documents/${num}/${year}/${d.file_type}`}
                    >
                      {d.file_type.toUpperCase()}
                    </a>
                  ))}
                </span>
              </div>
            ))}
        </div>
      )}
    </>
  );
}
