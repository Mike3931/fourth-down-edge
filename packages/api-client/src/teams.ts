import type { Stadium, Team } from '@fde/shared-types';

/**
 * Reference teams and venues. Team names appear as PLAIN TEXT ONLY — no NFL
 * or team logos anywhere in the product. Venue attributes are approximate and
 * part of the demonstration dataset.
 */

export interface TeamSeed {
  abbr: string;
  name: string;
  conference: 'AFC' | 'NFC';
  division: 'EAST' | 'NORTH' | 'SOUTH' | 'WEST';
  stadium: string;
  city: string;
  roof: 'OUTDOOR' | 'DOME' | 'RETRACTABLE';
  surface: 'GRASS' | 'TURF';
  tz: string;
  altitudeFt: number;
}

export const TEAM_SEEDS: TeamSeed[] = [
  { abbr: 'BUF', name: 'Buffalo Bills', conference: 'AFC', division: 'EAST', stadium: 'Highmark Stadium', city: 'Orchard Park, NY', roof: 'OUTDOOR', surface: 'TURF', tz: 'America/New_York', altitudeFt: 600 },
  { abbr: 'MIA', name: 'Miami Dolphins', conference: 'AFC', division: 'EAST', stadium: 'Hard Rock Stadium', city: 'Miami Gardens, FL', roof: 'OUTDOOR', surface: 'GRASS', tz: 'America/New_York', altitudeFt: 10 },
  { abbr: 'NE', name: 'New England Patriots', conference: 'AFC', division: 'EAST', stadium: 'Gillette Stadium', city: 'Foxborough, MA', roof: 'OUTDOOR', surface: 'TURF', tz: 'America/New_York', altitudeFt: 290 },
  { abbr: 'NYJ', name: 'New York Jets', conference: 'AFC', division: 'EAST', stadium: 'MetLife Stadium', city: 'East Rutherford, NJ', roof: 'OUTDOOR', surface: 'TURF', tz: 'America/New_York', altitudeFt: 10 },
  { abbr: 'BAL', name: 'Baltimore Ravens', conference: 'AFC', division: 'NORTH', stadium: 'M&T Bank Stadium', city: 'Baltimore, MD', roof: 'OUTDOOR', surface: 'GRASS', tz: 'America/New_York', altitudeFt: 50 },
  { abbr: 'CIN', name: 'Cincinnati Bengals', conference: 'AFC', division: 'NORTH', stadium: 'Paycor Stadium', city: 'Cincinnati, OH', roof: 'OUTDOOR', surface: 'TURF', tz: 'America/New_York', altitudeFt: 490 },
  { abbr: 'CLE', name: 'Cleveland Browns', conference: 'AFC', division: 'NORTH', stadium: 'Huntington Bank Field', city: 'Cleveland, OH', roof: 'OUTDOOR', surface: 'GRASS', tz: 'America/New_York', altitudeFt: 580 },
  { abbr: 'PIT', name: 'Pittsburgh Steelers', conference: 'AFC', division: 'NORTH', stadium: 'Acrisure Stadium', city: 'Pittsburgh, PA', roof: 'OUTDOOR', surface: 'GRASS', tz: 'America/New_York', altitudeFt: 730 },
  { abbr: 'HOU', name: 'Houston Texans', conference: 'AFC', division: 'SOUTH', stadium: 'NRG Stadium', city: 'Houston, TX', roof: 'RETRACTABLE', surface: 'TURF', tz: 'America/Chicago', altitudeFt: 50 },
  { abbr: 'IND', name: 'Indianapolis Colts', conference: 'AFC', division: 'SOUTH', stadium: 'Lucas Oil Stadium', city: 'Indianapolis, IN', roof: 'RETRACTABLE', surface: 'TURF', tz: 'America/Indiana/Indianapolis', altitudeFt: 720 },
  { abbr: 'JAX', name: 'Jacksonville Jaguars', conference: 'AFC', division: 'SOUTH', stadium: 'EverBank Stadium', city: 'Jacksonville, FL', roof: 'OUTDOOR', surface: 'GRASS', tz: 'America/New_York', altitudeFt: 10 },
  { abbr: 'TEN', name: 'Tennessee Titans', conference: 'AFC', division: 'SOUTH', stadium: 'Nissan Stadium', city: 'Nashville, TN', roof: 'OUTDOOR', surface: 'TURF', tz: 'America/Chicago', altitudeFt: 400 },
  { abbr: 'DEN', name: 'Denver Broncos', conference: 'AFC', division: 'WEST', stadium: 'Empower Field at Mile High', city: 'Denver, CO', roof: 'OUTDOOR', surface: 'GRASS', tz: 'America/Denver', altitudeFt: 5280 },
  { abbr: 'KC', name: 'Kansas City Chiefs', conference: 'AFC', division: 'WEST', stadium: 'GEHA Field at Arrowhead Stadium', city: 'Kansas City, MO', roof: 'OUTDOOR', surface: 'GRASS', tz: 'America/Chicago', altitudeFt: 900 },
  { abbr: 'LV', name: 'Las Vegas Raiders', conference: 'AFC', division: 'WEST', stadium: 'Allegiant Stadium', city: 'Las Vegas, NV', roof: 'DOME', surface: 'GRASS', tz: 'America/Los_Angeles', altitudeFt: 2000 },
  { abbr: 'LAC', name: 'Los Angeles Chargers', conference: 'AFC', division: 'WEST', stadium: 'SoFi Stadium', city: 'Inglewood, CA', roof: 'DOME', surface: 'TURF', tz: 'America/Los_Angeles', altitudeFt: 100 },
  { abbr: 'DAL', name: 'Dallas Cowboys', conference: 'NFC', division: 'EAST', stadium: 'AT&T Stadium', city: 'Arlington, TX', roof: 'RETRACTABLE', surface: 'TURF', tz: 'America/Chicago', altitudeFt: 600 },
  { abbr: 'NYG', name: 'New York Giants', conference: 'NFC', division: 'EAST', stadium: 'MetLife Stadium', city: 'East Rutherford, NJ', roof: 'OUTDOOR', surface: 'TURF', tz: 'America/New_York', altitudeFt: 10 },
  { abbr: 'PHI', name: 'Philadelphia Eagles', conference: 'NFC', division: 'EAST', stadium: 'Lincoln Financial Field', city: 'Philadelphia, PA', roof: 'OUTDOOR', surface: 'GRASS', tz: 'America/New_York', altitudeFt: 40 },
  { abbr: 'WAS', name: 'Washington Commanders', conference: 'NFC', division: 'EAST', stadium: 'Northwest Stadium', city: 'Landover, MD', roof: 'OUTDOOR', surface: 'GRASS', tz: 'America/New_York', altitudeFt: 200 },
  { abbr: 'CHI', name: 'Chicago Bears', conference: 'NFC', division: 'NORTH', stadium: 'Soldier Field', city: 'Chicago, IL', roof: 'OUTDOOR', surface: 'GRASS', tz: 'America/Chicago', altitudeFt: 590 },
  { abbr: 'DET', name: 'Detroit Lions', conference: 'NFC', division: 'NORTH', stadium: 'Ford Field', city: 'Detroit, MI', roof: 'DOME', surface: 'TURF', tz: 'America/Detroit', altitudeFt: 600 },
  { abbr: 'GB', name: 'Green Bay Packers', conference: 'NFC', division: 'NORTH', stadium: 'Lambeau Field', city: 'Green Bay, WI', roof: 'OUTDOOR', surface: 'GRASS', tz: 'America/Chicago', altitudeFt: 640 },
  { abbr: 'MIN', name: 'Minnesota Vikings', conference: 'NFC', division: 'NORTH', stadium: 'U.S. Bank Stadium', city: 'Minneapolis, MN', roof: 'DOME', surface: 'TURF', tz: 'America/Chicago', altitudeFt: 830 },
  { abbr: 'ATL', name: 'Atlanta Falcons', conference: 'NFC', division: 'SOUTH', stadium: 'Mercedes-Benz Stadium', city: 'Atlanta, GA', roof: 'RETRACTABLE', surface: 'TURF', tz: 'America/New_York', altitudeFt: 1000 },
  { abbr: 'CAR', name: 'Carolina Panthers', conference: 'NFC', division: 'SOUTH', stadium: 'Bank of America Stadium', city: 'Charlotte, NC', roof: 'OUTDOOR', surface: 'TURF', tz: 'America/New_York', altitudeFt: 750 },
  { abbr: 'NO', name: 'New Orleans Saints', conference: 'NFC', division: 'SOUTH', stadium: 'Caesars Superdome', city: 'New Orleans, LA', roof: 'DOME', surface: 'TURF', tz: 'America/Chicago', altitudeFt: 10 },
  { abbr: 'TB', name: 'Tampa Bay Buccaneers', conference: 'NFC', division: 'SOUTH', stadium: 'Raymond James Stadium', city: 'Tampa, FL', roof: 'OUTDOOR', surface: 'GRASS', tz: 'America/New_York', altitudeFt: 30 },
  { abbr: 'ARI', name: 'Arizona Cardinals', conference: 'NFC', division: 'WEST', stadium: 'State Farm Stadium', city: 'Glendale, AZ', roof: 'RETRACTABLE', surface: 'GRASS', tz: 'America/Phoenix', altitudeFt: 1070 },
  { abbr: 'LAR', name: 'Los Angeles Rams', conference: 'NFC', division: 'WEST', stadium: 'SoFi Stadium', city: 'Inglewood, CA', roof: 'DOME', surface: 'TURF', tz: 'America/Los_Angeles', altitudeFt: 100 },
  { abbr: 'SF', name: 'San Francisco 49ers', conference: 'NFC', division: 'WEST', stadium: "Levi's Stadium", city: 'Santa Clara, CA', roof: 'OUTDOOR', surface: 'GRASS', tz: 'America/Los_Angeles', altitudeFt: 20 },
  { abbr: 'SEA', name: 'Seattle Seahawks', conference: 'NFC', division: 'WEST', stadium: 'Lumen Field', city: 'Seattle, WA', roof: 'OUTDOOR', surface: 'TURF', tz: 'America/Los_Angeles', altitudeFt: 20 },
];

export function buildTeams(): { teams: Team[]; stadiums: Stadium[] } {
  const teams: Team[] = TEAM_SEEDS.map((t) => ({
    id: `team_${t.abbr}`,
    name: t.name,
    abbreviation: t.abbr,
    conference: t.conference,
    division: t.division,
  }));
  const stadiums: Stadium[] = TEAM_SEEDS.map((t) => ({
    id: `stad_${t.abbr}`,
    name: t.stadium,
    city: t.city,
    surface: t.surface,
    roof: t.roof,
    altitudeFt: t.altitudeFt,
    timezone: t.tz,
  }));
  return { teams, stadiums };
}
