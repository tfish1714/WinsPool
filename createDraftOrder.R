library(tidyverse)
library(dplyr)
library(plyr)
library(tidyr)
library(ggplot2)
library(reshape)
library(ggrepel)

seasonToLook <- 2020
players <- read_csv("C:\\Users\\Thomas Fischer\\Documents\\Gambling\\WinsPoolPlayers.csv")
draftOrder <- read_csv("C:\\Users\\Thomas Fischer\\Documents\\Gambling\\WinsPoolDraftOrder.csv")
draftResults <- read_csv("C:\\Users\\Thomas Fischer\\Documents\\Gambling\\WinsPoolDraftResults.csv")
draftOrderRules <- read.csv("C:\\Users\\Thomas Fischer\\Documents\\Gambling\\WinsPoolDraftOrderRules.csv")