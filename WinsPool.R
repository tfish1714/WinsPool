library(tidyverse)
library(dplyr)
library(plyr)
library(tidyr)
library(ggplot2)
library(reshape)
library(ggrepel)

#get all the data

nflDataGithub <- "https://raw.githubusercontent.com/leesharpe/nfldata/master/data/"
#nflDataGithub <- "C:\\Users\\Thomas Fischer\\Documents\\Gambling\\nfldata-master\\nfldata-master\\data\\"
localSave <- "C:\\Users\\Thomas Fischer\\Documents\\Gambling\\nfldata-master\\nfldata-master\\data\\"
test <- paste(localSave,"standings.csv",sep="")
standings <- read_csv(paste(nflDataGithub,"standings.csv",sep=""))
teams <- read_csv(paste(nflDataGithub,"teams.csv",sep=""))
games <- read_csv(paste(nflDataGithub,"games.csv",sep=""))
players <- read_csv("C:\\Users\\Thomas Fischer\\Documents\\Gambling\\WinsPool\\WinsPoolPlayers.csv")
draftOrder <- read_csv("C:\\Users\\Thomas Fischer\\Documents\\Gambling\\WinsPool\\WinsPoolDraftOrder.csv")
draftResults <- read_csv("C:\\Users\\Thomas Fischer\\Documents\\Gambling\\WinsPool\\WinsPoolDraftResults.csv")
draftOrderRules <- read.csv("C:\\Users\\Thomas Fischer\\Documents\\Gambling\\WinsPool\\WinsPoolDraftOrderRules.csv")

#convert to data frames
as.data.frame(games)
as.data.frame(draftResults)
as.data.frame(players)
as.data.frame(draftOrderRules)

my.result <- melt(draftOrderRules, id = c("ï..season","draftOrder")) %>%
  dplyr::rename(draftPick = value) %>%
  dplyr::rename(season = ï..season)

games$winner <- ifelse(games$result>0, games$home_team, ifelse(games$result<0, games$away_team,NA)) #same result
games$rec <- 1
games <- games %>%
  dplyr::group_by(season, winner,game_type) %>%
  dplyr::mutate(TotalWinsBySeason=cumsum(rec))%>%
  dplyr::select(-rec)

seasonToLook <- 2021

todayTeams <- teams %>% filter(season == seasonToLook)
todayStandings <- standings %>% filter(season == seasonToLook)
todayDraftResults <- draftResults %>% filter(season==seasonToLook)
todayGames <- games %>% 
  dplyr::filter(season==seasonToLook, week <=max(week)) %>% 
  dplyr::select(game_id, game_type, season, week, away_team, home_team, result, winner, TotalWinsBySeason) %>% 
  dplyr::rename(team = winner)
#head(todayDraftResults)
#head(todayGames)
#KC <- todayGames %>% filter(winner=="KC")

games_playerAdded <- join(todayGames,todayDraftResults,by=c("team","season"))
games_playerAdded <- na.omit(games_playerAdded)


games_playerAdded$rec <- 1
games_playerAddedWinsTotal <- games_playerAdded %>%
  dplyr::group_by(playerId) %>%
  dplyr::mutate(TotalPlayerWinsBySeason=cumsum(rec))%>%
  dplyr::select(-rec)

games_playerAddedWinsTotal <- join(games_playerAddedWinsTotal,players,by="playerId")

winsByWeekPlayer <- games_playerAddedWinsTotal %>% 
  dplyr::group_by(season,week, nickName) %>%
  dplyr::summarize(WeekWinTotal= max(TotalPlayerWinsBySeason))

winsByWeekPlayer <- winsByWeekPlayer %>%
  dplyr::ungroup() %>%
  tidyr::complete(season, week, nesting(nickName)) %>% 
  dplyr::group_by(nickName) %>%
  tidyr::fill("WeekWinTotal")

todayGames <- todayGames %>%
  dplyr::ungroup() %>%
  tidyr::complete(season, week, nesting(team)) %>% 
  dplyr::group_by(team) %>%
  tidyr::fill("TotalWinsBySeason")

winsByWeekPlayer[is.na(winsByWeekPlayer)] <- 0

#todayGames[is.na("TotalWinsBySeason")] <- 0
todayGames <- na.omit(todayGames)

winsPoolStandings <- join(todayStandings, todayDraftResults, by=c("team", "season")) %>%
  select(season, team, wins, losses, scored, allowed, draftPick, playerId)

winsPoolStandings <- join(winsPoolStandings, my.result, by=c("season","draftPick"))
winsPoolStandings <- winsPoolStandings %>%
  dplyr::group_by(season, playerId) %>%
  dplyr::mutate(my_ranks = order(order(wins, decreasing=TRUE))) %>%
  dplyr::mutate(ptDiff = (scored - allowed))


winsPoolStandingsPivotTotal <- winsPoolStandings %>%
  dplyr::group_by(season, playerId) %>%
  dplyr::summarize( TotalWins = sum(wins))


winsPoolStandingsPivotWins <- winsPoolStandings %>%
  dplyr::group_by(season, playerId, my_ranks) %>%
  dplyr::summarize(TotalWins = sum(wins)) %>%
  tidyr::spread(my_ranks, TotalWins)

colNames <- colnames(winsPoolStandingsPivotWins)




winsPoolStandingsptDiff <- winsPoolStandings %>%
  dplyr::group_by(season, playerId, my_ranks) %>%
  dplyr::summarize( PtDiff = sum(ptDiff)) %>%
  tidyr::spread(my_ranks, PtDiff)

write.csv(winsPoolStandings,paste(localSave,"test.csv",sep=""))

data_endsplayerPlot <- winsByWeekPlayer %>% filter(week == max(week))

playerWinsPlotTitle <- paste("Player Total wins by Week - ",seasonToLook,sep="")
playerWinPlot <- ggplot(winsByWeekPlayer,aes(x=week,y=WeekWinTotal)) +
  # theme_minimal() +
  geom_line(aes(color=nickName)) +
  geom_point(position = position_dodge(0.2), size = 1) +
  xlab("Week") +
  ylab("Totals Wins") +
  labs(title=playerWinsPlotTitle)
playerWinPlot + 
  geom_text_repel(
    aes(label = paste(nickName,WeekWinTotal,sep="-")), data = data_endsplayerPlot,
    color = "black", size = 3
  )

data_endsTeamPlot <- todayGames %>% filter(week == max(week))
teamWinsPlotTitle <- paste("Team Total wins by Week - ",seasonToLook,sep="")
teamWinPlot <- ggplot(todayGames,aes(x=week,y=TotalWinsBySeason)) +
  #theme_minimal() +
  geom_line(aes(color=team)) +
  geom_point(position = position_dodge(0.2), size = 1) +
  xlab("Week") +
  ylab("Totals Wins") +
  labs(title=teamWinsPlotTitle)

teamWinPlot + 
  geom_text_repel(
    aes(label = paste(team,TotalWinsBySeason,sep="-")), data = data_endsTeamPlot,
    color = "black", size = 3
  )


