{-# OPTIONS_GHC -O0 #-}
module Functions where

-- Combinators
fId :: a -> a
fId x = x

fConst :: a -> b -> a
fConst x _ = x

fFlip :: (a -> b -> c) -> b -> a -> c
fFlip f y x = f x y

fComp :: (b -> c) -> (a -> b) -> a -> c
fComp f g x = f (g x)

fFst :: (a, b) -> a
fFst (x, _) = x

fSnd :: (a, b) -> b
fSnd (_, y) = y

fCurry :: ((a, b) -> c) -> a -> b -> c
fCurry f x y = f (x, y)

fUncurry :: (a -> b -> c) -> (a, b) -> c
fUncurry f (x, y) = f x y

-- Boolean
fNot :: Bool -> Bool
fNot True  = False
fNot False = True

fAnd :: Bool -> Bool -> Bool
fAnd True  y = y
fAnd False _ = False

fOr :: Bool -> Bool -> Bool
fOr True  _ = True
fOr False y = y

fBoolToInt :: Bool -> Int
fBoolToInt True  = 1
fBoolToInt False = 0

-- Int arithmetic
fSucc :: Int -> Int
fSucc n = n + 1

fPred :: Int -> Int
fPred n = n - 1

fDouble :: Int -> Int
fDouble n = n + n

fSquare :: Int -> Int
fSquare n = n * n

fAbs :: Int -> Int
fAbs n = if n < 0 then 0 - n else n

fMax :: Int -> Int -> Int
fMax x y = if x >= y then x else y

fMin :: Int -> Int -> Int
fMin x y = if x <= y then x else y

fEven :: Int -> Bool
fEven n = (n - 2 * (n `div` 2)) == 0

fOdd :: Int -> Bool
fOdd n = fNot (fEven n)

fGcd :: Int -> Int -> Int
fGcd x 0 = x
fGcd x y = fGcd y (x - y * (x `div` y))

-- List construction
fSingleton :: a -> [a]
fSingleton x = [x]

fCons :: a -> [a] -> [a]
fCons x xs = x : xs

fAppend :: [a] -> [a] -> [a]
fAppend []     ys = ys
fAppend (x:xs) ys = x : fAppend xs ys

fReplicate :: Int -> a -> [a]
fReplicate 0 _ = []
fReplicate n x = x : fReplicate (n - 1) x

fRange :: Int -> Int -> [Int]
fRange lo hi
  | lo > hi   = []
  | otherwise = lo : fRange (lo + 1) hi

-- List deconstruction
fNull :: [a] -> Bool
fNull []    = True
fNull (_:_) = False

fHead :: [a] -> a
fHead (x:_) = x
fHead []    = fHead []

fTail :: [a] -> [a]
fTail (_:xs) = xs
fTail []     = []

fLast :: [a] -> a
fLast [x]    = x
fLast (_:xs) = fLast xs
fLast []     = fLast []

fInit :: [a] -> [a]
fInit [_]    = []
fInit (x:xs) = x : fInit xs
fInit []     = []

fLength :: [a] -> Int
fLength []     = 0
fLength (_:xs) = 1 + fLength xs

-- Folds
fFoldr :: (a -> b -> b) -> b -> [a] -> b
fFoldr _ z []     = z
fFoldr f z (x:xs) = f x (fFoldr f z xs)

fFoldl :: (b -> a -> b) -> b -> [a] -> b
fFoldl _ z []     = z
fFoldl f z (x:xs) = fFoldl f (f z x) xs

fSum :: [Int] -> Int
fSum []     = 0
fSum (x:xs) = x + fSum xs

fProduct :: [Int] -> Int
fProduct []     = 1
fProduct (x:xs) = x * fProduct xs

fMaximum :: [Int] -> Int
fMaximum [x]    = x
fMaximum (x:xs) = fMax x (fMaximum xs)
fMaximum []     = 0

fMinimum :: [Int] -> Int
fMinimum [x]    = x
fMinimum (x:xs) = fMin x (fMinimum xs)
fMinimum []     = 0

fAndList :: [Bool] -> Bool
fAndList []     = True
fAndList (x:xs) = fAnd x (fAndList xs)

fOrList :: [Bool] -> Bool
fOrList []     = False
fOrList (x:xs) = fOr x (fOrList xs)

-- List transforms
fMap :: (a -> b) -> [a] -> [b]
fMap _ []     = []
fMap f (x:xs) = f x : fMap f xs

fFilter :: (a -> Bool) -> [a] -> [a]
fFilter _ []     = []
fFilter p (x:xs)
  | p x       = x : fFilter p xs
  | otherwise = fFilter p xs

fReverse :: [a] -> [a]
fReverse = go []
  where
    go acc []     = acc
    go acc (x:xs) = go (x:acc) xs

fConcat :: [[a]] -> [a]
fConcat []       = []
fConcat (xs:xss) = fAppend xs (fConcat xss)

fConcatMap :: (a -> [b]) -> [a] -> [b]
fConcatMap _ []     = []
fConcatMap f (x:xs) = fAppend (f x) (fConcatMap f xs)

fIntersperse :: a -> [a] -> [a]
fIntersperse _ []     = []
fIntersperse _ [x]    = [x]
fIntersperse sep (x:xs) = x : sep : fIntersperse sep xs

-- List indexing
fTake :: Int -> [a] -> [a]
fTake 0 _      = []
fTake _ []     = []
fTake n (x:xs) = x : fTake (n-1) xs

fDrop :: Int -> [a] -> [a]
fDrop 0 xs     = xs
fDrop _ []     = []
fDrop n (_:xs) = fDrop (n-1) xs

fSplitAt :: Int -> [a] -> ([a], [a])
fSplitAt 0 xs     = ([], xs)
fSplitAt _ []     = ([], [])
fSplitAt n (x:xs) = let (a, b) = fSplitAt (n-1) xs in (x:a, b)

fTakeWhile :: (a -> Bool) -> [a] -> [a]
fTakeWhile _ []     = []
fTakeWhile p (x:xs)
  | p x       = x : fTakeWhile p xs
  | otherwise = []

fDropWhile :: (a -> Bool) -> [a] -> [a]
fDropWhile _ []     = []
fDropWhile p xs@(x:xs')
  | p x       = fDropWhile p xs'
  | otherwise = xs

fSpan :: (a -> Bool) -> [a] -> ([a], [a])
fSpan _ []     = ([], [])
fSpan p xs@(x:xs')
  | p x       = let (a, b) = fSpan p xs' in (x:a, b)
  | otherwise = ([], xs)

-- List search
fElem :: Int -> [Int] -> Bool
fElem _ []     = False
fElem y (x:xs) = (x == y) || fElem y xs

fNotElem :: Int -> [Int] -> Bool
fNotElem y xs = fNot (fElem y xs)

fLookup :: Int -> [(Int, a)] -> Maybe a
fLookup _ []          = Nothing
fLookup k ((k2,v):rest)
  | k == k2   = Just v
  | otherwise = fLookup k rest

fFind :: (a -> Bool) -> [a] -> Maybe a
fFind _ []     = Nothing
fFind p (x:xs)
  | p x       = Just x
  | otherwise = fFind p xs

-- List zipping
fZip :: [a] -> [b] -> [(a, b)]
fZip []     _      = []
fZip _      []     = []
fZip (x:xs) (y:ys) = (x, y) : fZip xs ys

fZipWith :: (a -> b -> c) -> [a] -> [b] -> [c]
fZipWith _ []     _      = []
fZipWith _ _      []     = []
fZipWith f (x:xs) (y:ys) = f x y : fZipWith f xs ys

fUnzip :: [(a, b)] -> ([a], [b])
fUnzip []          = ([], [])
fUnzip ((x, y):ps) = let (xs, ys) = fUnzip ps in (x:xs, y:ys)

-- List advanced
fPartition :: (a -> Bool) -> [a] -> ([a], [a])
fPartition _ []     = ([], [])
fPartition p (x:xs)
  | p x       = let (yes, no) = fPartition p xs in (x:yes, no)
  | otherwise = let (yes, no) = fPartition p xs in (yes, x:no)

fNubInt :: [Int] -> [Int]
fNubInt []     = []
fNubInt (x:xs) = x : fNubInt (fFilter (\y -> fNot (x == y)) xs)

fInsert :: Int -> [Int] -> [Int]
fInsert x []     = [x]
fInsert x (y:ys)
  | x <= y    = x : y : ys
  | otherwise = y : fInsert x ys

fSort :: [Int] -> [Int]
fSort []     = []
fSort (x:xs) = fInsert x (fSort xs)

-- Maybe
fMaybe :: b -> (a -> b) -> Maybe a -> b
fMaybe def _ Nothing  = def
fMaybe _   f (Just x) = f x

fIsJust :: Maybe a -> Bool
fIsJust (Just _) = True
fIsJust Nothing  = False

fIsNothing :: Maybe a -> Bool
fIsNothing Nothing  = True
fIsNothing (Just _) = False

fFromMaybe :: a -> Maybe a -> a
fFromMaybe def Nothing  = def
fFromMaybe _   (Just x) = x

fMaybeToList :: Maybe a -> [a]
fMaybeToList Nothing  = []
fMaybeToList (Just x) = [x]

fListToMaybe :: [a] -> Maybe a
fListToMaybe []    = Nothing
fListToMaybe (x:_) = Just x

fCatMaybes :: [Maybe a] -> [a]
fCatMaybes []             = []
fCatMaybes (Nothing  : ms) = fCatMaybes ms
fCatMaybes (Just x   : ms) = x : fCatMaybes ms

fMapMaybe :: (a -> Maybe b) -> [a] -> [b]
fMapMaybe _ []     = []
fMapMaybe f (x:xs) = case f x of
  Nothing -> fMapMaybe f xs
  Just y  -> y : fMapMaybe f xs

-- Arithmetic combinators
fNegate :: Int -> Int
fNegate n = 0 - n

fTriple :: Int -> Int
fTriple n = n + n + n

fHalve :: Int -> Int
fHalve n = n `div` 2

fMod :: Int -> Int -> Int
fMod x y = x - y * (x `div` y)

fDivides :: Int -> Int -> Bool
fDivides d n = fMod n d == 0

fIsZero :: Int -> Bool
fIsZero n = n == 0

fIsPositive :: Int -> Bool
fIsPositive n = n > 0

fIsNegative :: Int -> Bool
fIsNegative n = n < 0

fSignum :: Int -> Int
fSignum n = if n > 0 then 1 else if n < 0 then (0 - 1) else 0

fClamp :: Int -> Int -> Int -> Int
fClamp lo hi n = fMax lo (fMin hi n)

fAvg :: Int -> Int -> Int
fAvg x y = (x + y) `div` 2

fPow :: Int -> Int -> Int
fPow _ 0 = 1
fPow x n = x * fPow x (n - 1)

fFactorial :: Int -> Int
fFactorial 0 = 1
fFactorial n = n * fFactorial (n - 1)

fFibonacci :: Int -> Int
fFibonacci 0 = 0
fFibonacci 1 = 1
fFibonacci n = fFibonacci (n - 1) + fFibonacci (n - 2)

-- Boolean combinators
fXor :: Bool -> Bool -> Bool
fXor True  False = True
fXor False True  = True
fXor _     _     = False

fImplies :: Bool -> Bool -> Bool
fImplies True  y = y
fImplies False _ = True

fNand :: Bool -> Bool -> Bool
fNand x y = fNot (fAnd x y)

fNor :: Bool -> Bool -> Bool
fNor x y = fNot (fOr x y)

fIfThenElse :: Bool -> a -> a -> a
fIfThenElse True  t _ = t
fIfThenElse False _ f = f

fBoolEq :: Bool -> Bool -> Bool
fBoolEq True  True  = True
fBoolEq False False = True
fBoolEq _     _     = False

-- Pair / tuple operations
fSwap :: (a, b) -> (b, a)
fSwap (x, y) = (y, x)

fBoth :: (a -> b) -> (a, a) -> (b, b)
fBoth f (x, y) = (f x, f y)

fMapFst :: (a -> c) -> (a, b) -> (c, b)
fMapFst f (x, y) = (f x, y)

fMapSnd :: (b -> c) -> (a, b) -> (a, c)
fMapSnd f (x, y) = (x, f y)

fBimap :: (a -> c) -> (b -> d) -> (a, b) -> (c, d)
fBimap f g (x, y) = (f x, g y)

fDup :: a -> (a, a)
fDup x = (x, x)

fPairWith :: (a -> b) -> a -> (a, b)
fPairWith f x = (x, f x)

fUncurry3 :: (a -> b -> c -> d) -> (a, b, c) -> d
fUncurry3 f (x, y, z) = f x y z

fTriple3 :: a -> b -> c -> (a, b, c)
fTriple3 x y z = (x, y, z)

fFst3 :: (a, b, c) -> a
fFst3 (x, _, _) = x

fSnd3 :: (a, b, c) -> b
fSnd3 (_, y, _) = y

fThd3 :: (a, b, c) -> c
fThd3 (_, _, z) = z

-- List recursion patterns
fAny :: (a -> Bool) -> [a] -> Bool
fAny _ []     = False
fAny p (x:xs) = p x || fAny p xs

fAll :: (a -> Bool) -> [a] -> Bool
fAll _ []     = True
fAll p (x:xs) = p x && fAll p xs

fCount :: (a -> Bool) -> [a] -> Int
fCount _ []     = 0
fCount p (x:xs) = (if p x then 1 else 0) + fCount p xs

fSumWith :: (a -> Int) -> [a] -> Int
fSumWith _ []     = 0
fSumWith f (x:xs) = f x + fSumWith f xs

fHeadOrDefault :: a -> [a] -> a
fHeadOrDefault def []    = def
fHeadOrDefault _   (x:_) = x

fLastOrDefault :: a -> [a] -> a
fLastOrDefault def []     = def
fLastOrDefault _   [x]    = x
fLastOrDefault def (_:xs) = fLastOrDefault def xs

fScanl1 :: (a -> a -> a) -> [a] -> [a]
fScanl1 _ []     = []
fScanl1 f (x:xs) = go x xs
  where
    go acc []     = [acc]
    go acc (y:ys) = acc : go (f acc y) ys

fIterate :: (a -> a) -> a -> Int -> [a]
fIterate _ _ 0 = []
fIterate f x n = x : fIterate f (f x) (n - 1)

fUnfoldr :: (b -> Maybe (a, b)) -> b -> [a]
fUnfoldr f b = case f b of
  Nothing     -> []
  Just (a, b') -> a : fUnfoldr f b'

fGroupBy :: (a -> a -> Bool) -> [a] -> [[a]]
fGroupBy _ []     = []
fGroupBy eq (x:xs) =
  let (same, rest) = fSpan (eq x) xs
  in (x : same) : fGroupBy eq rest

fChunksOf :: Int -> [a] -> [[a]]
fChunksOf _ [] = []
fChunksOf n xs = fTake n xs : fChunksOf n (fDrop n xs)

fTranspose :: [[a]] -> [[a]]
fTranspose []       = []
fTranspose ([] : _) = []
fTranspose xss      = fMap fHead xss : fTranspose (fMap fTail xss)

fIndexOf :: Int -> [a] -> Maybe a
fIndexOf _ []     = Nothing
fIndexOf 0 (x:_)  = Just x
fIndexOf n (_:xs) = fIndexOf (n - 1) xs

fZipWith3 :: (a -> b -> c -> d) -> [a] -> [b] -> [c] -> [d]
fZipWith3 _ []     _      _      = []
fZipWith3 _ _      []     _      = []
fZipWith3 _ _      _      []     = []
fZipWith3 f (x:xs) (y:ys) (z:zs) = f x y z : fZipWith3 f xs ys zs

fZip3 :: [a] -> [b] -> [c] -> [(a, b, c)]
fZip3 []     _      _      = []
fZip3 _      []     _      = []
fZip3 _      _      []     = []
fZip3 (x:xs) (y:ys) (z:zs) = (x, y, z) : fZip3 xs ys zs

fUnzip3 :: [(a, b, c)] -> ([a], [b], [c])
fUnzip3 []            = ([], [], [])
fUnzip3 ((x,y,z):ps)  = let (xs,ys,zs) = fUnzip3 ps in (x:xs, y:ys, z:zs)

fMapAccumL :: (acc -> a -> (acc, b)) -> acc -> [a] -> (acc, [b])
fMapAccumL _ acc []     = (acc, [])
fMapAccumL f acc (x:xs) =
  let (acc', y)   = f acc x
      (acc'', ys) = fMapAccumL f acc' xs
  in (acc'', y : ys)

fEnumerate :: [a] -> [(Int, a)]
fEnumerate = go 0
  where
    go _ []     = []
    go n (x:xs) = (n, x) : go (n + 1) xs

fFoldlWithIndex :: (b -> Int -> a -> b) -> b -> [a] -> b
fFoldlWithIndex f z xs = fFoldl (\acc (i, x) -> f acc i x) z (fEnumerate xs)

-- Maybe chains
fOrElse :: Maybe a -> a -> a
fOrElse Nothing  def = def
fOrElse (Just x) _   = x

fMaybeBind :: Maybe a -> (a -> Maybe b) -> Maybe b
fMaybeBind Nothing  _ = Nothing
fMaybeBind (Just x) f = f x

fMaybeMap :: (a -> b) -> Maybe a -> Maybe b
fMaybeMap _ Nothing  = Nothing
fMaybeMap f (Just x) = Just (f x)

fMaybeFilter :: (a -> Bool) -> Maybe a -> Maybe a
fMaybeFilter _ Nothing  = Nothing
fMaybeFilter p (Just x) = if p x then Just x else Nothing

fMaybeOr :: Maybe a -> Maybe a -> Maybe a
fMaybeOr (Just x) _ = Just x
fMaybeOr Nothing  y = y

fSequenceMaybe :: [Maybe a] -> Maybe [a]
fSequenceMaybe []             = Just []
fSequenceMaybe (Nothing  : _) = Nothing
fSequenceMaybe (Just x   : ms) = case fSequenceMaybe ms of
  Nothing -> Nothing
  Just xs -> Just (x : xs)

fMapMaybeList :: (a -> Maybe b) -> [a] -> Maybe [b]
fMapMaybeList _ []     = Just []
fMapMaybeList f (x:xs) = case f x of
  Nothing -> Nothing
  Just y  -> case fMapMaybeList f xs of
    Nothing -> Nothing
    Just ys -> Just (y : ys)

fFirstJust :: [Maybe a] -> Maybe a
fFirstJust []             = Nothing
fFirstJust (Just x  : _)  = Just x
fFirstJust (Nothing : ms) = fFirstJust ms

fCountJust :: [Maybe a] -> Int
fCountJust []             = 0
fCountJust (Just _  : ms) = 1 + fCountJust ms
fCountJust (Nothing : ms) = fCountJust ms

-- Composition patterns
fOn :: (b -> b -> c) -> (a -> b) -> a -> a -> c
fOn op f x y = op (f x) (f y)

fCompose2 :: (c -> d) -> (a -> b -> c) -> a -> b -> d
fCompose2 f g x y = f (g x y)

fApply :: (a -> b) -> a -> b
fApply f x = f x

fConstFlip :: b -> a -> b
fConstFlip y _ = y

fFix :: (a -> a) -> a
fFix f = let x = f x in x

fUntil :: (a -> Bool) -> (a -> a) -> a -> a
fUntil p f x = if p x then x else fUntil p f (f x)

fCompose3 :: (c -> d) -> (b -> c) -> (a -> b) -> a -> d
fCompose3 f g h x = f (g (h x))

fTwice :: (a -> a) -> a -> a
fTwice f x = f (f x)

fNTimes :: Int -> (a -> a) -> a -> a
fNTimes 0 _ x = x
fNTimes n f x = fNTimes (n - 1) f (f x)

-- String / Char operations (using [Char] = String)
fDigitToInt :: Char -> Int
fDigitToInt c = fromEnum c - fromEnum '0'

fIntToDigit :: Int -> Char
fIntToDigit n = toEnum (n + fromEnum '0')

fIsDigit :: Char -> Bool
fIsDigit c = c >= '0' && c <= '9'

fIsAlpha :: Char -> Bool
fIsAlpha c = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')

fToUpper :: Char -> Char
fToUpper c = if c >= 'a' && c <= 'z'
             then toEnum (fromEnum c - 32)
             else c

fToLower :: Char -> Char
fToLower c = if c >= 'A' && c <= 'Z'
             then toEnum (fromEnum c + 32)
             else c

fWords :: String -> [String]
fWords [] = []
fWords s  = let (w, rest) = fSpan (/= ' ') (fDropWhile (== ' ') s)
            in if fNull w then [] else w : fWords rest

fUnwords :: [String] -> String
fUnwords []     = []
fUnwords [w]    = w
fUnwords (w:ws) = w ++ (' ' : fUnwords ws)

fLines :: String -> [String]
fLines []     = []
fLines (c:cs)
  | c == '\n' = [] : fLines cs
  | otherwise = case fLines cs of
      []     -> [[c]]
      (l:ls) -> (c : l) : ls

-- Int list derived operations
fCountTrue :: [Bool] -> Int
fCountTrue = fCount fId

fSumSquares :: [Int] -> Int
fSumSquares xs = fSum (fMap fSquare xs)

fSumPositive :: [Int] -> Int
fSumPositive xs = fSum (fFilter fIsPositive xs)

fMaxOr :: Int -> [Int] -> Int
fMaxOr def [] = def
fMaxOr _   xs = fMaximum xs

fMinOr :: Int -> [Int] -> Int
fMinOr def [] = def
fMinOr _   xs = fMinimum xs

fHeadOrZero :: [Int] -> Int
fHeadOrZero []    = 0
fHeadOrZero (x:_) = x

fSumOf :: (a -> Int) -> [a] -> Int
fSumOf = fSumWith

fZipWithAdd :: [Int] -> [Int] -> [Int]
fZipWithAdd xs ys = fZipWith (\x y -> x + y) xs ys

fZipWithMul :: [Int] -> [Int] -> [Int]
fZipWithMul = fZipWith (*)

fDotProduct :: [Int] -> [Int] -> Int
fDotProduct xs ys = fSum (fZipWithMul xs ys)

fNegate2 :: [Int] -> [Int]
fNegate2 = fMap fNegate

fAbsList :: [Int] -> [Int]
fAbsList = fMap fAbs

fDoubleAll :: [Int] -> [Int]
fDoubleAll = fMap fDouble

fIncrAll :: [Int] -> [Int]
fIncrAll = fMap fSucc

fDecrAll :: [Int] -> [Int]
fDecrAll = fMap fPred
